"""P0-5 regression test: DB and API ports in docker-compose.yml must bind to 127.0.0.1."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _service_ports(compose_text: str) -> dict[str, list[str]]:
    services: dict[str, list[str]] = {}
    current_svc = None
    in_ports = False
    for line in compose_text.splitlines():
        m_svc = re.match(r"^  ([a-zA-Z0-9_-]+):\s*$", line)
        if m_svc:
            current_svc = m_svc.group(1)
            services.setdefault(current_svc, [])
            in_ports = False
            continue
        if current_svc and re.match(r"^    ports:\s*$", line):
            in_ports = True
            continue
        if in_ports:
            m_port = re.match(r'^\s*-\s*["\']?([^"\']+)["\']?\s*$', line)
            if m_port:
                services[current_svc].append(m_port.group(1).strip())
            elif re.match(r"^    [a-zA-Z0-9_-]+:", line) or re.match(r"^[a-zA-Z0-9_-]+:", line):
                in_ports = False
    return services


def test_db_and_api_ports_bind_to_localhost_only():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    ports = _service_ports(text)
    for svc in ("db", "api"):
        assert ports.get(svc), f"{svc} has no published ports in docker-compose.yml"
        for mapping in ports[svc]:
            assert mapping.startswith("127.0.0.1:"), (
                f"{svc} port mapping {mapping!r} is exposed on 0.0.0.0; must start with '127.0.0.1:'"
            )
