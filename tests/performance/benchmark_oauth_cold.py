"""OAuth grant・通知・本当に新しいprocessの初回MCPを隔離DBだけで測る。"""

import json
import logging
import os
import subprocess
import sys

from starlette.testclient import TestClient

from app.core.config import settings
from app.core.mcp import CLIENT_ID
from app.main import create_app
from scripts.summarize_cpu import summarize
from scripts.verification import safe_environment
from tests.helpers.auth import authorize_as
from tests.performance.benchmark_mcp_cpu import DESTINATION, Collector
from tests.routers.test_mcp import connect

# 外部資格情報はargv/envへ載せず、使い捨てDBのtokenだけをstdinで渡す。
CHILD = r"""
import json, logging, os, sys
from time import process_time_ns, perf_counter_ns
from urllib.parse import urlsplit
url=urlsplit(os.environ['DATABASE_URL'])
assert url.hostname in {'127.0.0.1', 'localhost'}
assert url.path == '/syncnesto_test' and url.port not in {None, 0, 5432}
request=json.load(sys.stdin)
cpu,wall=process_time_ns(),perf_counter_ns()
from app.main import app
startup={'cpu_ms':(process_time_ns()-cpu)/1e6,
         'wall_ms':(perf_counter_ns()-wall)/1e6}
from starlette.testclient import TestClient
rows=[]
class Collector(logging.Handler):
    def emit(self,record): rows.append(json.loads(record.getMessage()))
log=logging.getLogger('app.core.cpu_diagnostics')
log.addHandler(Collector()); log.setLevel(logging.INFO)
top=[]
class Observer:
    def __init__(self, wrapped): self.wrapped, self.first = wrapped, True
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not self.first or not request.get('profile'):
            return await self.wrapped(scope, receive, send)
        self.first=False
        import cProfile, pstats
        profile=cProfile.Profile(timer=process_time_ns,timeunit=1e-9)
        profile.enable()
        try: await self.wrapped(scope, receive, send)
        finally:
            profile.disable()
            stats=sorted(getattr(pstats.Stats(profile),'stats').items(),
                         key=lambda x:x[1][3],reverse=True)[:45]
            top.extend([{'file':k[0].split('/')[-1], 'line':k[1],
              'function':k[2], 'calls':v[1], 'cumulative_cpu_ms':v[3]*1000}
              for k,v in stats])
with TestClient(Observer(app), base_url='http://127.0.0.1:8000') as client:
    headers={'Authorization':'Bearer '+request['token'],
             'Accept':'application/json, text/event-stream',
             'MCP-Protocol-Version':'2025-11-25'}
    for index in range(4):
        response=client.post('/mcp',json={'jsonrpc':'2.0','id':1,
          'method':'tools/call','params':{'name':'list_projects','arguments':{}}},
          headers=headers)
        assert response.status_code==200 and 'error' not in response.json()
        assert not response.json()['result'].get('isError')
print(json.dumps({'startup':startup,'first':rows[0],'warm':rows[1:],
                  'first_event_loop_profile':top}))
"""


def test_oauth_and_cold(
    db, monkeypatch, create_test_user, create_test_project, assign_project_role
):
    """本番のrefresh tokenは一切使わず、回転後の新tokenを次の標本へ渡す。"""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "cpu_diagnostics_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8000/mcp")
    actor, project = create_test_user(), create_test_project()
    assign_project_role(user=actor, project=project, role_key="manager")
    collector = Collector()
    log = logging.getLogger("app.core.cpu_diagnostics")
    log.addHandler(collector)
    output = {}
    try:
        with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
            log.setLevel(logging.INFO)
            authorize_as(client, actor, domain="127.0.0.1")
            _, tokens, _ = connect(client, project.id)
            output["authorization_code_grant"] = collector.rows[-1]
            assert output["authorization_code_grant"]["endpoint"] == "/oauth/token"
            client.cookies.clear()
            rows = []
            for index in range(13):
                response = client.post(
                    "/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "resource": settings.mcp_resource_url,
                        "refresh_token": tokens["refresh_token"],
                    },
                )
                assert response.status_code == 200
                tokens = response.json()
                if index:
                    rows.append(collector.rows[-1])
            output["refresh_grant"] = {"samples": rows, "summary": summarize(rows)}
            rows = []
            for _ in range(12):
                response = client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={
                        "Authorization": "Bearer " + tokens["access_token"],
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2025-11-25",
                    },
                )
                assert response.status_code == 202
                rows.append(collector.rows[-1])
            output["initialized_notification"] = {
                "samples": rows,
                "summary": summarize(rows),
            }
            env = safe_environment()
            env.update(
                DATABASE_URL=os.environ["DATABASE_URL"],
                MCP_ENABLED="true",
                MCP_ISSUER_URL=settings.mcp_issuer_url,
                MCP_RESOURCE_URL=settings.mcp_resource_url,
                CPU_DIAGNOSTICS_ENABLED="true",
                LOG_LEVEL="ERROR",
            )
            cold = []
            for _ in range(5):
                result = subprocess.run(
                    [sys.executable, "-c", CHILD],
                    env=env,
                    input=json.dumps({"token": tokens["access_token"]}),
                    text=True,
                    capture_output=True,
                    timeout=60,
                )
                assert result.returncode == 0, result.stderr[-1500:]
                cold.append(json.loads(result.stdout.strip().splitlines()[-1]))
            output["cold_processes"] = cold
    finally:
        log.removeHandler(collector)
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "oauth-cold.json").write_text(json.dumps(output, indent=2))


def test_cold_event_loop_profile(
    db, monkeypatch, create_test_user, create_test_project, assign_project_role
):
    """初回CPUの大きさを説明するため、別標本でASGI event loopをprofileする。"""
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_issuer_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(settings, "mcp_resource_url", "http://127.0.0.1:8000/mcp")
    actor, project = create_test_user(), create_test_project()
    assign_project_role(user=actor, project=project, role_key="manager")
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as client:
        authorize_as(client, actor, domain="127.0.0.1")
        _, tokens, _ = connect(client, project.id)
    env = safe_environment()
    env.update(
        DATABASE_URL=os.environ["DATABASE_URL"],
        MCP_ENABLED="true",
        MCP_ISSUER_URL=settings.mcp_issuer_url,
        MCP_RESOURCE_URL=settings.mcp_resource_url,
        CPU_DIAGNOSTICS_ENABLED="true",
        LOG_LEVEL="ERROR",
    )
    result = subprocess.run(
        [sys.executable, "-c", CHILD],
        env=env,
        input=json.dumps({"token": tokens["access_token"], "profile": True}),
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, "isolated profiling subprocess failed"
    output = json.loads(result.stdout.strip().splitlines()[-1])
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "cold-event-loop-profile.json").write_text(
        json.dumps(output, indent=2)
    )
