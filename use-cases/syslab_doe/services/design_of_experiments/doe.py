"""Design of Experiments: decide which runs to do, write experiment_design.json.

Based on the ERIGrid 2.0 "Toolbox with DoE and SA methods" (toolbox_start.py),
https://github.com/ERIGrid2/toolbox_doe_sa, MIT License, Copyright (c) 2023 ERIGrid 2.0
(license text: LICENSE-ERIGrid-toolbox in the use case folder). Modified for the AI-Effect
orchestrator: the request layout (YAML, doe_conf, nested factor paths) and the design file
are new; the sampling is unchanged.

Only the *sampling* half lives here: given factors and
their ranges, produce the list of runs. No simulation, no analysis, no plots.

    JSON request  ──►  doe.py  ──►  experiment_design.json  +  JSON result
    (factors,                       (one entry per run)        (where it went)
     ranges, method)

Request (JSON)
-------------------------------------------------------------
{
  "experiment_id":      "exp-001",   # optional
  "doe_type":           "LHS",       # which method, see METHODS below
  "samples":            10,          # required by the sampling methods
  "seed":               42,          # optional, makes random methods reproducible
  "add_extreme_points": false,       # sobol only: also add the 2^k corners
  "variations_dict": {               # the factors: entity -> parameter -> spec
    "heating":  {"supply_temp": [65, 85],                                   # uniform [min, max]
                 "flow_rate": {"type": "norm", "mean": 3.5, "stdvs": 0.5}}, # normal
    "consumer": {"load": {"type": "discrete", "set": [50, 100, 200]}}       # fixed values
  },
  "entities_parameters": {...},      # optional baseline, copied into every recipe
  "basic_conf":          {...},      # optional run settings, copied into every recipe
  "target_metrics":      ["..."]     # optional, echoed for the analysis step
}

Any other key of the toolbox configs (plots, folder_figures, parallelize, ...)
is ignored, so the original toolbox configuration files are valid requests.

Request layout (requests/doe_request_template.yaml)
----------------------------------------------------
The request is JSON or YAML. The layout above is the original toolbox's and still works. The template adds:
  doe_conf             the DoE settings (doe_type, samples, seed, add_extreme_points) instead of top-level keys
  scenario_name,       run settings at the top level, copied into every recipe (basic_conf still works too)
  target
  entities_parameters  the baseline: every variable that can be changed, in groups of any depth. It is not
                       interpreted: time series, topology and so on are passed on as they are
  variations_dict      mirrors those paths to any depth. Every path must exist in entities_parameters,
                       with a number or null (null means not set)

Methods (k = number of factors, N = samples)
--------------------------------------------
  doe_type                   factor types                         runs
  extreme_points             uniform                              2^k
  OAT                        uniform                              1 + 2k
  sobol                      uniform                              2^ceil(log2 N), + 2^k with add_extreme_points
  LHS                        norm only                            N
  sobol_indices              uniform, norm                        N (k + 2)
  fast                       uniform, norm                        N k            (N > 64)
  distribution_and_discrete  uniform and/or norm, plus exactly    3 N
                             one discrete factor of 3 integers

Result (JSON, printed to stdout) — a DataReference
--------------------------------------------------
{
  "protocol":   "file",
  "uri":        "/abs/path/out/experiment_design_exp-001.json",
  "format":     "json",
  "size_bytes": 1234,
  "checksum":   "sha256:...",
  "metadata":   {"experiment_id": "exp-001", "doe_type": "LHS", "n_runs": 10}
}

Usage
-----
    python doe.py --request test_inputs/lhs.json --out-dir ./out
    cat request.json | python doe.py --request - --out-dir ./out
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from scipy.stats import norm, qmc

SCHEMA_VERSION = "0.3"
OUTPUT_FILENAME = "experiment_design.json"


# --------------------------------------------------------------------------- #
# factors
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Factor:
    """One input that the experiment varies, e.g. dh_network.T_supply_grid."""

    path: tuple[str, ...]  # where the value sits in entities_parameters, e.g. ("input_timeseries", "q_demand_kw", "scale")
    distribution: str  # "uniform" | "norm" | "discrete"
    spec: dict[str, Any]  # uniform: min/max, norm: mean/stdvs, discrete: set

    @property
    def name(self) -> str:
        return ".".join(self.path)

    @property
    def entity(self) -> str:
        return self.path[0]

    @property
    def parameter(self) -> str:
        return ".".join(self.path[1:])

    def describe(self) -> dict:
        return {
            "name": self.name,
            "entity": self.entity,
            "parameter": self.parameter,
            "path": list(self.path),
            "distribution": self.distribution,
            **self.spec,
        }

    def from_unit(self, u: np.ndarray) -> np.ndarray:
        """Map values in [0, 1] onto this factor's range or distribution."""
        if self.distribution == "uniform":
            return self.spec["min"] + u * (self.spec["max"] - self.spec["min"])
        if self.distribution == "norm":
            return norm.ppf(u, loc=self.spec["mean"], scale=self.spec["stdvs"])
        raise ValueError(f"{self.name}: cannot scale a {self.distribution} factor")


def parse_factors(variations_dict: Any) -> list[Factor]:
    """Turn the toolbox's variations_dict into a flat, ordered list of factors."""
    if not isinstance(variations_dict, dict) or not variations_dict:
        raise ValueError("'variations_dict' must be a non-empty object: {group: {parameter: spec}}, in groups of any depth")
    factors: list[Factor] = []
    _collect_factors(variations_dict, (), factors)
    return factors


def _collect_factors(node: dict, path: tuple[str, ...], factors: list[Factor]) -> None:
    """Walk a (nested) variations_dict in order; a factor is a [min, max] range or a {'type': ...} spec."""
    for key, value in node.items():
        here = path + (str(key),)
        if isinstance(value, list) or (isinstance(value, dict) and "type" in value):
            factors.append(_parse_spec(here, value))
        elif isinstance(value, dict) and value:
            _collect_factors(value, here, factors)
        else:
            where = ".".join(here)
            raise ValueError(f"{where}: expected [min, max], a {{'type': ...}} spec, or a group of those, got {value!r}")


def _parse_spec(path: tuple[str, ...], spec: Any) -> Factor:
    where = ".".join(path)
    if isinstance(spec, list):
        if len(spec) != 2 or not all(_is_number(v) for v in spec):
            raise ValueError(f"{where}: a range must be [min, max], got {spec!r}")
        lo, hi = spec
        if not lo < hi:
            raise ValueError(f"{where}: min must be smaller than max, got {spec!r}")
        return Factor(path, "uniform", {"min": lo, "max": hi})
    if isinstance(spec, dict):
        kind = spec.get("type")
        if kind == "norm":
            mean, stdvs = spec.get("mean"), spec.get("stdvs")
            if not (_is_number(mean) and _is_number(stdvs) and stdvs > 0):
                raise ValueError(f"{where}: a normal factor needs numeric 'mean' and 'stdvs' > 0, got {spec!r}")
            return Factor(path, "norm", {"mean": mean, "stdvs": stdvs})
        if kind == "discrete":
            values = spec.get("set")
            if not isinstance(values, list) or not values:
                raise ValueError(f"{where}: a discrete factor needs a non-empty 'set' list, got {spec!r}")
            return Factor(path, "discrete", {"set": values})
        raise ValueError(f"{where}: unknown factor type {kind!r} (expected 'norm' or 'discrete')")
    raise ValueError(f"{where}: expected [min, max] or {{'type': ...}}, got {spec!r}")


# --------------------------------------------------------------------------- #
# sampling methods: each returns (rows, notes); a row holds one value per factor
# --------------------------------------------------------------------------- #
Rows = list[list[Any]]


def _extreme_points(factors: list[Factor], request: dict) -> tuple[Rows, list[str]]:
    """Full factorial over every factor's min and max."""
    levels = [(f.spec["min"], f.spec["max"]) for f in factors]
    return [list(combo) for combo in itertools.product(*levels)], []


def _oat(factors: list[Factor], request: dict) -> tuple[Rows, list[str]]:
    """One baseline run, then each factor alone at its min and then at its max.

    Factors that are not being varied keep their value from entities_parameters.
    Without one the value is None and the run leaves it to the simulator default.
    """
    baseline = [_baseline_value(request, f) for f in factors]
    notes = [
        f"OAT: no baseline for {f.name} in entities_parameters; the simulator default applies when it is not varied"
        for f, value in zip(factors, baseline)
        if value is None
    ]
    rows = [list(baseline)]
    for i, f in enumerate(factors):
        for level in (f.spec["min"], f.spec["max"]):
            row = list(baseline)
            row[i] = level
            rows.append(row)
    return rows, notes


def _sobol(factors: list[Factor], request: dict) -> tuple[Rows, list[str]]:
    """Unscrambled Sobol' sequence (scipy) scaled onto the ranges, optionally plus the corners.

    As in the original: the sample count is rounded up to the next power of 2, and
    add_extreme_points appends all 2^k corners of the ranges.
    """
    n = _samples(request)
    m = math.log2(n)
    notes = []
    if not m.is_integer():
        m = int(m) + 1
        notes.append(f"sobol: samples rounded up from {n} to {2**m}; the original toolbox warns "
                     "'unbalanced design requested since number of samples is not a power of 2' in this case")
    unit = qmc.Sobol(d=len(factors), scramble=False).random_base2(m=int(m))
    if request.get("add_extreme_points"):
        corners = np.array(list(itertools.product([0, 1], repeat=len(factors))))
        unit = np.concatenate((unit, corners), axis=0)
        notes.append("sobol: add_extreme_points appends all 2^k corners, as the original code does (its comment says "
                     "'SOBOL index includes 0 extreme point, so only add \"1\"', but the code adds every corner); the "
                     "all-minimum corner is already the first point of the sequence, so it appears twice")
    return _scale(factors, unit), notes


def _lhs(factors: list[Factor], request: dict) -> tuple[Rows, list[str]]:
    """Latin Hypercube from pyDOE, mapped through each factor's normal distribution.

    As in the original, this is for normal factors only.
    """
    n = _samples(request)
    unit = _pydoe_lhs(len(factors), n, request.get("seed"))
    return _scale(factors, unit), []


def _sobol_indices(factors: list[Factor], request: dict) -> tuple[Rows, list[str]]:
    """Saltelli-style design from SALib, so first and total Sobol' indices can be estimated later."""
    from SALib.sample import sobol as salib_sobol

    n = _samples(request)
    notes = []
    if n & (n - 1):
        notes.append(f"sobol_indices: samples={n} is not a power of 2; the index estimates converge best when it is")
    samples = salib_sobol.sample(_salib_problem(factors), n, calc_second_order=False, seed=request.get("seed"))
    return _to_rows(samples), notes


def _fast(factors: list[Factor], request: dict) -> tuple[Rows, list[str]]:
    """eFAST design from SALib: each factor oscillates at its own frequency."""
    from SALib.sample import fast_sampler

    n = _samples(request)
    if n <= 64:
        raise ValueError(f"fast needs samples > 64 (4 * M^2 with M = 4), got {n}")
    samples = fast_sampler.sample(_salib_problem(factors), n, M=4, seed=request.get("seed"))
    return _to_rows(samples), []


def _distribution_and_discrete(factors: list[Factor], request: dict) -> tuple[Rows, list[str]]:
    """SALib Latin Hypercube over the continuous factors, repeated once for each value of the discrete factor.

    As in the original, this works for exactly one discrete factor with exactly 3
    integer values. The N Latin Hypercube points are used three times, so the design
    has 3 N runs: the first N with the first value, the next N with the second, and
    so on.
    """
    from SALib.sample import latin

    discrete = [f for f in factors if f.distribution == "discrete"]
    continuous = [f for f in factors if f.distribution != "discrete"]
    if len(discrete) != 1 or len(discrete[0].spec["set"]) != 3:
        raise ValueError("distribution_and_discrete needs exactly one discrete factor with exactly 3 values "
                         "(the original toolbox only works for that case)")
    if not continuous:
        raise ValueError("distribution_and_discrete needs at least one continuous (uniform or norm) factor")
    values = discrete[0].spec["set"]
    if not all(_is_number(v) and float(v).is_integer() for v in values):
        raise ValueError(f"{discrete[0].name}: the discrete values must be integers (the original toolbox "
                         f"truncates them to integers, so anything else is refused), got {values!r}")

    points = latin.sample(_salib_problem(continuous), _samples(request), seed=request.get("seed"))
    rows = []
    for value in values:
        for point in points:
            by_name = {f.name: float(x) for f, x in zip(continuous, point)}
            by_name[discrete[0].name] = float(value)
            rows.append([by_name[f.name] for f in factors])
    return rows, []


METHODS: dict[str, tuple[Callable[[list[Factor], dict], tuple[Rows, list[str]]], set[str]]] = {
    "extreme_points": (_extreme_points, {"uniform"}),
    "OAT": (_oat, {"uniform"}),
    "sobol": (_sobol, {"uniform"}),
    "LHS": (_lhs, {"norm"}),
    "sobol_indices": (_sobol_indices, {"uniform", "norm"}),
    "fast": (_fast, {"uniform", "norm"}),
    "distribution_and_discrete": (_distribution_and_discrete, {"uniform", "norm", "discrete"}),
}


# --------------------------------------------------------------------------- #
# the service
# --------------------------------------------------------------------------- #
def design_experiment(request: dict) -> dict:
    """Build the experiment design (see module docstring). Raises ValueError for a bad request."""
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    request = _normalise(request)
    method = request.get("doe_type")
    if method not in METHODS:
        raise ValueError(f"'doe_type' must be one of {list(METHODS)}, got {method!r}")
    seed = request.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise ValueError(f"'seed' must be an integer, got {seed!r}")

    sampler, supported = METHODS[method]
    factors = parse_factors(request.get("variations_dict"))
    _check_against_baseline(factors, request.get("entities_parameters"))
    for f in factors:
        if f.distribution not in supported:
            raise ValueError(
                f"{f.name}: {f.distribution} type factors are not supported by doe_type '{method}' "
                f"(supported: {', '.join(sorted(supported))})"
            )

    rows, notes = sampler(factors, request)

    runs = []
    for i, row in enumerate(rows):
        run_no = str(i).zfill(2)  # as in the original: run_00 ... run_99, run_100, ...
        runs.append(
            {
                "run_id": f"run_{run_no}",
                "factor_values": {f.name: value for f, value in zip(factors, row)},
                "recipe": _recipe(request, factors, row, run_no),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": request.get("experiment_id"),
        "scenario_name": (request.get("basic_conf") or {}).get("scenario_name"),
        "target": (request.get("basic_conf") or {}).get("target"),
        "doe_type": method,
        "samples_requested": request.get("samples"),
        "seed": seed,
        "n_runs": len(runs),
        "factors": [f.describe() for f in factors],
        "target_metrics": request.get("target_metrics", []),
        "notes": notes,
        "runs": runs,
    }


def output_filename(experiment_id: Any) -> str:
    """experiment_design_<experiment_id>.json, or experiment_design.json when there is no usable id.

    The id comes from the request, so anything but letters, digits, '-', '_' and '.' is replaced,
    which keeps the file inside the output folder.
    """
    if experiment_id is None:
        return OUTPUT_FILENAME
    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_" for c in str(experiment_id))
    safe = safe.strip(".")[:100]
    return f"experiment_design_{safe}.json" if safe else OUTPUT_FILENAME


def run(request: dict, out_dir: str | pathlib.Path) -> dict:
    """Write the design into ``out_dir`` (see output_filename) and return a DataReference to it."""
    design = design_experiment(request)
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / output_filename(design["experiment_id"])
    data = json.dumps(design, indent=2, allow_nan=False).encode("utf-8")
    path.write_bytes(data)
    return {
        "protocol": "file",
        "uri": str(path.resolve()),
        "format": "json",
        "size_bytes": len(data),
        "checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
        "metadata": {
            "experiment_id": design["experiment_id"],
            "doe_type": design["doe_type"],
            "n_runs": design["n_runs"],
        },
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _samples(request: dict) -> int:
    n = request.get("samples")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"'samples' must be a positive integer for doe_type '{request['doe_type']}', got {n!r}")
    return n


_MISSING = object()


def _lookup(baseline: Any, path: tuple[str, ...]) -> Any:
    """The value at ``path`` in the nested baseline, or _MISSING."""
    node = baseline
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _baseline_value(request: dict, factor: Factor) -> Any:
    value = _lookup(request.get("entities_parameters") or {}, factor.path)
    return None if value is _MISSING else value


def _check_against_baseline(factors: list[Factor], baseline: Any) -> None:
    """Every factor must vary a number (or an unset null) that is listed in entities_parameters."""
    for f in factors:
        value = _lookup(baseline or {}, f.path)
        if value is _MISSING:
            raise ValueError(f"{f.name}: not listed in entities_parameters; a factor can only vary a variable "
                             "that the baseline lists (use null there for 'not set')")
        if value is not None and not _is_number(value):
            raise ValueError(f"{f.name}: the baseline value {value!r} is not a number or null, so it cannot be varied")


def _normalise(request: dict) -> dict:
    """The request as the sampling code reads it: the DoE settings from doe_conf (or, as in the original
    toolbox configs, from the top level) and the run settings (scenario_name, target) gathered in basic_conf."""
    conf = request.get("doe_conf")
    if conf is not None and not isinstance(conf, dict):
        raise ValueError("'doe_conf' must be an object")
    basic = request.get("basic_conf")
    if basic is not None and not isinstance(basic, dict):
        raise ValueError("'basic_conf' must be an object")
    flat = {**request, **(conf or {})}
    flat["basic_conf"] = {**(basic or {}), **{k: request[k] for k in ("scenario_name", "target") if k in request}}
    return flat


def _pydoe_lhs(n_factors: int, n_samples: int, seed: int | None) -> np.ndarray:
    """pyDOE's Latin Hypercube. pyDOE draws from numpy's global random state, so a seed is applied
    around the call and the previous state is restored afterwards."""
    try:
        import pyDOE
    except ImportError as e:
        raise ImportError("doe_type 'LHS' needs the pyDOE package (pyDOE==0.3.8, as in the original toolbox)") from e
    if seed is None:
        return pyDOE.lhs(n_factors, samples=n_samples)
    state = np.random.get_state()
    try:
        np.random.seed(seed)
        return pyDOE.lhs(n_factors, samples=n_samples)
    finally:
        np.random.set_state(state)


def _scale(factors: list[Factor], unit: np.ndarray) -> Rows:
    columns = [f.from_unit(unit[:, j]) for j, f in enumerate(factors)]
    return [[float(column[i]) for column in columns] for i in range(unit.shape[0])]


def _to_rows(samples: np.ndarray) -> Rows:
    return [[float(v) for v in row] for row in samples]


def _salib_problem(factors: list[Factor]) -> dict:
    return {
        "num_vars": len(factors),
        "names": [f.name for f in factors],
        "dists": ["unif" if f.distribution == "uniform" else "norm" for f in factors],
        "bounds": [
            [f.spec["min"], f.spec["max"]] if f.distribution == "uniform" else [f.spec["mean"], f.spec["stdvs"]]
            for f in factors
        ],
    }


def _recipe(request: dict, factors: list[Factor], row: list[Any], run_no: str) -> dict:
    """Full parameter set for one run, as the toolbox's recipes.json: the run settings
    and baseline parameters, with this run's factor values written on top."""
    recipe = {
        **copy.deepcopy(request.get("basic_conf") or {}),
        **copy.deepcopy(request.get("entities_parameters") or {}),
    }
    recipe["ID"] = run_no
    for f, value in zip(factors, row):
        if value is None:
            continue
        node = recipe
        for key in f.path[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                raise ValueError(f"cannot set {f.name}: '{key}' in the baseline is not an object")
        node[f.path[-1]] = value
    return recipe


def parse_request_text(text: str) -> Any:
    """A request document: JSON, or YAML (see requests/doe_request_template.yaml)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"the request is neither valid JSON nor valid YAML: {e}") from e


def _load_request(arg: str) -> dict:
    text = sys.stdin.read() if arg == "-" else pathlib.Path(arg).read_text(encoding="utf-8")
    return parse_request_text(text)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--request", required=True, help="path to a JSON or YAML request, or - for stdin")
    p.add_argument("--out-dir", default="./out", help="directory to write the design into")
    args = p.parse_args(argv)

    try:
        result = run(_load_request(args.request), args.out_dir)
    except (ValueError, OSError) as e:  # JSONDecodeError is a ValueError
        json.dump({"error": str(e)}, sys.stdout, indent=2)
        print()
        return 1

    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
