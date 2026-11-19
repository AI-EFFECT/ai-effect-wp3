# Actual renderer input consumption — 19 November 2026

Source base `39405313f9b685d91b8263b9b87a3fc42577331d`; instrumented serving source `4fada6cad733c932d6a877338798718142e24e85`, branch `codex/renderer-consumption-20261119`.

The real Danish RenderTopologies worker emits input_consumption schema_version1.0/kind json-networks-render-v1. It hashes the actual parsed whole input and networks array using JSON sort_keys=True, separators=(",",":"), allow_nan=False (default ensure_ascii=True), then hashes each network at the NetworkDrawing.render call boundary. Only completed calls increment processed_network_count. The observation includes workflow_id/task_id/operation_name and an explicit software-only instrumentation statement. Algorithm and renderer behaviour are unchanged; existing upstream_execution/artifact manifests remain separate.

Portal maps the observed task/node key to the immutable owned submission/instance and checks exact references, parsed input digest, ordered network digests/count, output counts and receipt hashes. No service-ID exception or delivery-derived parameter claim is introduced. Existing output/replay/authentication guards remain.

Eighteen focused native Control regressions passed using actual enumerate_networks/NetworkDrawing, disposable fixture data and storage. They include different maximum settings, invalid settings before task creation/algorithm call and old output preservation. Windows checkout now uses deterministic LF for vendored parameter_contract.py files; all copies retain identical Git blobs and semantics. Initial test-directory sandbox failures and line-ending-only failures were resolved before acceptance freeze.

A fresh isolated qualification invoked real generator/renderer source and verified both renderer input/per-network digests. This is setup qualification, not browser workflow acceptance or scientific approval. The paired Portal genuine exercise stopped at J2 after an invalid model response; zero designer workflows ran. No result from that exercise is claimed here. Later documentation commits do not change the frozen serving source.
