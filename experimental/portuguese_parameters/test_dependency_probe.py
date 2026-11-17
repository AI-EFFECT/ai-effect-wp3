"""Actual probe with HTTP/Redis doubles; no algorithm routes or journal writes."""
import httpx
import pytest
from .dependency_probe import dependency_probe, TransientProbeError, PermanentProbeError
from .runtime import capabilities


class Redis:
    def ping(self): return True
    def scan_iter(self, pattern): return []


def test_probe_only_uses_inspection_endpoints_and_no_authority(tmp_path):
    requests = []
    def response(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/openapi.json": return httpx.Response(200, json={"paths":{p:{} for p in ["/train","/generate","/models"]}})
        return httpx.Response(200, json=capabilities() if request.url.path == "/control/capabilities" else {"status":"ok"})
    dependency_probe(Redis(), "test-token", "http://ml", "http://observer", "http://adapter", transport=httpx.MockTransport(response))
    assert requests == [("GET","/openapi.json"),("GET","/health"),("GET","/control/capabilities")]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status,exception", [(503,TransientProbeError),(401,PermanentProbeError),(403,PermanentProbeError),(302,PermanentProbeError),(404,PermanentProbeError)])
def test_startup_status_is_distinct_from_auth_and_contract_errors(status, exception):
    with pytest.raises(exception):
        dependency_probe(Redis(), "test", "http://ml", "http://observer", "http://adapter", transport=httpx.MockTransport(lambda _: httpx.Response(status)))


def test_capability_mismatch_is_permanent_and_actionable():
    def response(request):
        if request.url.path == "/openapi.json": return httpx.Response(200,json={"paths":{p:{} for p in ["/train","/generate","/models"]}})
        return httpx.Response(200,json={})
    with pytest.raises(PermanentProbeError, match="capability/contract mismatch"):
        dependency_probe(Redis(), "test", "http://ml", "http://observer", "http://adapter", transport=httpx.MockTransport(response))


def test_connection_failure_is_transient_without_exposing_credentials():
    def fail(_): raise httpx.ConnectError("private network diagnostic")
    with pytest.raises(TransientProbeError, match="connection/DNS/timeout") as exc:
        dependency_probe(Redis(), "test", "http://ml", "http://observer", "http://adapter", transport=httpx.MockTransport(fail))
    assert "private network" not in str(exc.value)


def test_late_probe_cannot_become_healthy_and_cycle_fits_lease():
    from .dependency_probe import PROBE_SECONDS, POLL_SECONDS
    from .supervision import LEASE_SECONDS
    assert PROBE_SECONDS+1+POLL_SECONDS < LEASE_SECONDS
    clock=[0]
    def response(request):
        clock[0]+=1.1
        if request.url.path=="/openapi.json":return httpx.Response(200,json={"paths":{p:{} for p in ["/train","/generate","/models"]}})
        return httpx.Response(200,json=capabilities() if request.url.path=="/control/capabilities" else {})
    with pytest.raises(TransientProbeError,match="budget"):
        dependency_probe(Redis(),"test","http://ml","http://observer","http://adapter",transport=httpx.MockTransport(response),monotonic=lambda:clock[0])


def test_queue_guards_before_first_lease_and_at_exact_bound_workflow():
    import json
    class Queues(Redis):
        def scan_iter(self, pattern):return ["workflow:wf-owned"] if pattern=="workflow:*" else ["queue:wf-owned"]
        def get(self, key):return json.dumps({"status":"running"})
        def llen(self, key):return 1
    def response(request):
        if request.url.path=="/openapi.json":return httpx.Response(200,json={"paths":{p:{} for p in ["/train","/generate","/models"]}})
        return httpx.Response(200,json=capabilities() if request.url.path=="/control/capabilities" else {})
    args=(Queues(),"test","http://ml","http://observer","http://adapter")
    with pytest.raises(PermanentProbeError,match="Unresolved unrelated workflow"):
        dependency_probe(*args,transport=httpx.MockTransport(response))
    # Ongoing health renewal neither claims nor inspects a just-enqueued root.
    dependency_probe(*args,check_queues=False,transport=httpx.MockTransport(response))
    dependency_probe(*args,bound="wf-owned",transport=httpx.MockTransport(response))
    with pytest.raises(PermanentProbeError,match="Unresolved unrelated workflow"):
        dependency_probe(*args,bound="wf-other",transport=httpx.MockTransport(response))
