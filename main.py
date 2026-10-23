```python
#!/usr/bin/env python3
"""
Multi-Service Health Check Alert Script
Usage: python health_check.py --config health_checks.yaml [--json] [--silent] [--timeout 10]
       python health_check.py --url https://api.example.com/health --name "API Gateway"
"""

import argparse
import json
import logging
import socket
import ssl
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class Status(Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"


@dataclass
class CheckResult:
    name: str
    status: Status
    response_time_ms: float
    message: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class HealthCheck:
    name: str
    url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    expected_status: int = 200
    timeout: float = 5.0
    check_type: str = "http"
    expected_text: Optional[str] = None
    alert_threshold: int = 3
    _consecutive_failures: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "HealthCheck":
        return cls(
            name=data.get("name", "unnamed"),
            url=data.get("url"),
            host=data.get("host"),
            port=data.get("port"),
            expected_status=data.get("expected_status", 200),
            timeout=data.get("timeout", 5.0),
            check_type=data.get("check_type", "http"),
            expected_text=data.get("expected_text"),
            alert_threshold=data.get("alert_threshold", 3),
        )


def setup_logging(verbose: bool = False, silent: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    if silent:
        level = logging.CRITICAL + 1

    logger = logging.getLogger("health_check")
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="health_check",
        description="Multi-Service Health Check Alert Script for cloud/DevOps engineers.",
        epilog="Example: %(prog)s --config checks.yaml --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to YAML config file with service definitions.",
    )
    parser.add_argument(
        "--url",
        help="Single HTTP(S) URL to check.",
    )
    parser.add_argument(
        "--name",
        default="unnamed",
        help="Name for a single --url check (default: 'unnamed').",
    )
    parser.add_argument(
        "--host",
        help="Host for a TCP health check.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port for a TCP health check.",
    )
    parser.add_argument(
        "--expected-status",
        type=int,
        default=200,
        help="Expected HTTP status code (default: 200).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout in seconds for each check (default: 5.0).",
    )
    parser.add_argument(
        "--check-type",
        choices=["http", "https", "tcp"],
        default="http",
        help="Type of health check (default: http).",
    )
    parser.add_argument(
        "--alert-threshold",
        type=int,
        default=3,
        help="Number of consecutive failures before alerting (default: 3).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Run checks this many times with 5s intervals (default: 1).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between iterations when --iterations > 1 (default: 5.0).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Suppress all log output.",
    )
    parser.add_argument(
        "--expected-text",
        help="Expected substring in HTTP response body.",
    )
    return parser.parse_args()


def check_http(url: str, expected_status: int, timeout: float, expected_text: Optional[str] = None,
               logger: Optional[logging.Logger] = None) -> CheckResult:
    start = time.monotonic()
    try:
        req = Request(url, headers={"User-Agent": "health-checker/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed = (time.monotonic() - start) * 1000
            if resp.status != expected_status:
                msg = f"Expected status {expected_status}, got {resp.status}"
                if logger:
                    logger.debug("%s", msg)
                return CheckResult(name="", status=Status.UNHEALTHY,
                                   response_time_ms=round(elapsed, 2), message=msg)
            if expected_text and expected_text not in body:
                msg = f"Expected text '{expected_text}' not found in response"
                if logger:
                    logger.debug("%s", msg)
                return CheckResult(name="", status=Status.UNHEALTHY,
                                   response_time_ms=round(elapsed, 2), message=msg)
            msg = f"OK (HTTP {resp.status}, {round(elapsed, 2)}ms)"
            if logger:
                logger.debug("%s", msg)
            return CheckResult(name="", status=Status.HEALTHY,
                               response_time_ms=round(elapsed, 2), message=msg)
    except HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        msg = f"HTTP {e.code}: {e.reason}"
        if logger:
            logger.warning("%s", msg)
        return CheckResult(name="", status=Status.UNHEALTHY,
                           response_time_ms=round(elapsed, 2), message=msg)
    except TimeoutError:
        elapsed = (time.monotonic() - start) * 1000
        msg = f"Request timed out after {timeout}s"
        if logger:
            logger.warning("%s", msg)
        return CheckResult(name="", status=Status.TIMEOUT,
                           response_time_ms=round(elapsed, 2), message=msg)
    except URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        msg = f"Connection failed: {e.reason}"
        if logger:
            logger.warning("%s", msg)
        return CheckResult(name="", status=Status.UNHEALTHY,
                           response_time_ms=round(elapsed, 2), message=msg)
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        msg = f"Unexpected error: {type(e).__name__}: {e}"
        if logger:
            logger.error("%s", msg)
        return CheckResult(name="", status=Status.UNKNOWN,
                           response_time_ms=round(elapsed, 2), message=msg)


def check_tcp(host: str, port: int, timeout: float, logger: Optional[logging.Logger] = None) -> CheckResult:
    start = time.monotonic()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        ssl_ctx = None
        if host.startswith("ssl:") or port == 443:
            ssl_ctx = ssl.create_default_context()
        try:
            if ssl_ctx:
                with ssl_ctx.wrap_socket(sock, server_hostname=host.replace("ssl:", "")) as ssock:
                    ssock.connect((host.replace("ssl:", ""), port))
            else:
                sock.connect((host, port))
            elapsed = (time.monotonic() - start) * 1000
            msg = f"OK (TCP {host}:{port}, {round(elapsed, 2)}ms)"
            if logger:
                logger.debug("%s", msg)
            return CheckResult(name="", status=Status.HEALTHY,
                               response_time_ms=round(elapsed, 2), message=msg)
        finally:
            sock.close()
    except socket.timeout:
        elapsed = (time.monotonic() - start) * 1000
        msg = f"TCP connection to {host}:{port} timed out after {timeout}s"
        if logger:
            logger.warning("%s", msg)
        return CheckResult(name="", status=Status.TIMEOUT,
                           response_time_ms=round(elapsed, 2), message=msg)
    except OSError as e:
        elapsed = (time.monotonic() - start) * 1000
        msg = f"TCP connection failed: {e}"
        if logger:
            logger.warning("%s", msg)
        return CheckResult(name="", status=Status.UNHEALTHY,
                           response_time_ms=round(elapsed, 2), message=msg)
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        msg = f"Unexpected error: {type(e).__name__}: {e}"
        if logger:
            logger.error("%s", msg)
        return CheckResult(name="", status=Status.UNKNOWN,
                           response_time_ms=round(elapsed, 2), message=msg)


def run_check(check: HealthCheck, logger: logging.Logger) -> CheckResult:
    check.name = check.name
    if check.check_type in ("http", "https"):
        url = check.url
        if not url:
            return CheckResult(name=check.name, status=Status.UNKNOWN,
                               response_time_ms=0.0, message="No URL provided")
        result = check_http(url, check.expected_status, check.timeout, check.expected_text, logger)
    elif check.check_type == "tcp":
        host = check.host
        port = check.port
        if not host or port is None:
            return CheckResult(name=check.name, status=Status.UNKNOWN,
                               response_time_ms=0.0, message="Host and port required for TCP check")
        result = check_tcp(host, port, check.timeout, logger)
    else:
        result = CheckResult(name=check.name, status=Status.UNKNOWN,
                             response_time_ms=0.0, message=f"Unknown check_type: {check.check_type}")
    result.name = check.name
    return result


def load_config(path: Path, logger: logging.Logger) -> list[HealthCheck]:
    if not path.exists():
        logger.error("Config file not found: %s", path)
        sys.exit(1)

    content = path.read_text(encoding="utf-8")

    if path.suffix in (".yml", ".yaml"):
        if not HAS_YAML:
            logger.error("PyYAML is required for YAML configs. Install with: pip install pyyaml")
            sys.exit(1)
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            logger.error("Failed to parse YAML config: %s", e)
            sys.exit(1)
    elif path.suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON config: %s", e)
            sys.exit(1)
    else:
        logger.error("Unsupported config format. Use .yaml, .yml, or .json")
        sys.exit(1)

    checks = []
    services = data.get("services", [])
    default_timeout = data.get("timeout", 5.0)
    default_expected_status = data.get("expected_status", 200)
    default_check_type = data.get("check_type", "http")
    default_alert_threshold = data.get("alert_threshold", 3)

    for svc in services:
        check = HealthCheck.from_dict(svc)
        check.timeout = svc.get("timeout", default_timeout)
        check.expected_status = svc.get("expected_status", default_expected_status)
        check.check_type = svc.get("check_type", default_check_type)
        check.alert_threshold = svc.get("alert_threshold", default_alert_threshold)
        checks.append(check)

    logger.info("Loaded %d service check(s) from %s", len(checks), path)
    return checks


def build_single_check(args: argparse.Namespace, logger: logging.Logger) -> list[HealthCheck]:
    checks = []
    if args.url:
        check = HealthCheck(
            name=args.name,
            url=args.url,
            expected_status=args.expected_status,
            timeout=args.timeout,
            check_type=args.check_type,
            expected_text=args.expected_text,
            alert_threshold=args.alert_threshold,
        )
        checks.append(check)
        logger.info("Added HTTP(S) check: %s -> %s", check.name, check.url)
    elif args.host and args.port:
        check = HealthCheck(
            name=args.name,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            check_type="tcp",
            alert_threshold=args.alert_threshold,
        )
        checks.append(check)
        logger.info("Added TCP check: %s -> %s:%s", check.name, check.host, check.port)
    else:
        logger.error("Provide --config <file> or --url/--host and --port")
        sys.exit(1)
    return checks


def format_human(results: list[CheckResult]) -> str:
    lines = []
    separator = "─" * 72
    lines.append(separator)
    lines.append(f"{'Service':<30} {'Status':<12} {'Latency':<12} {'Message'}")
    lines.append(separator)
    for r in results:
        icon = {"healthy": "✓", "unhealthy": "✗", "timeout": "⏱", "unknown": "?"}[r.status.value]
        lines.append(
            f"{r.name:<30} {icon} {r.status.value:<12} {r.response_time_ms:>8.2f}ms  {r.message}"
        )
    lines.append(separator)
    healthy = sum(1 for r in results if r.status == Status.HEALTHY)
    total = len(results)
    lines.append(f"Results: {healthy}/{total} healthy  |  {datetime.utcnow().isoformat()}Z")
    return "\n".join(lines)


def detect_alerts(results: list[CheckResult], checks_map: dict[str, HealthCheck]) -> list[str]:
    alerts = []
    for r in results:
        check = checks_map.get(r.name)
        if not check:
            continue
        if r.status in (Status.UNHEALTHY, Status.TIMEOUT):
            check._consecutive_failures += 1
            if check._consecutive_failures >= check.alert_threshold:
                alerts.append(
                    f"ALERT [{r.name}]: {r.status.value.upper()} "
                    f"({check._consecutive_failures}/{check.alert_threshold} failures) — {r.message}"
                )
        else:
            check._consecutive_failures = 0
    return alerts


def main() -> int:
    args = parse_args()
    logger = setup_logging(verbose=args.verbose, silent=args.silent)

    if args.config:
        checks = load_config(args.config, logger)
    else:
        checks = build_single_check(args, logger)

    checks_map = {c.name: c for c in checks}
    all_results: list[CheckResult] = []
    all_alerts: list[str] = []

    for iteration in range(1, args.iterations + 1):
        logger.info("=== Iteration %d/%d ===", iteration, args.iterations)
        round_results: list[CheckResult] = []

        for check in checks:
            logger.debug("Checking %s (%s)...", check.name, check.check_type)
            result = run_check(check, logger)
            round_results.append(result)

        all_results.extend(round_results)

        if args.iterations > 1 and iteration < args.iterations:
            time.sleep(args.interval)

    if args.json:
        output = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "iterations": args.iterations,
            "results": [r.to_dict() for r in all_results],
        }
        if all_results:
            healthy = sum(1 for r in all_results if r.status == Status.HEALTHY)
            output["summary"] = {
                "total": len(all_results),
                "healthy": healthy,
                "unhealthy": sum(1 for r in all_results if r.status == Status.UNHEALTHY),
                "timeout": sum(1 for r in all_results if r.status == Status.TIMEOUT),
                "unknown": sum(1 for r in all_results if r.status == Status.UNKNOWN),
            }
        print(json.dumps(output, indent=2))
    else:
        last_round = all_results[-len(checks):] if args.iterations > 1 else all_results
        print(format_human(last_round))
        alerts = detect_alerts(last_round, checks_map)
        all_alerts.extend(alerts)
        if all_alerts:
            print("\n" + "\n".join(all_alerts))

    has_failures = any(r.status != Status.HEALTHY for r in (all_results[-len(checks):]
                                                              if args.iterations > 1 else all_results))
    return 1 if has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
```