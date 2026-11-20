import base64
import re


def decode(value):
    try:
        return base64.b64decode(value, validate=True).decode(errors="replace")
    except ValueError:
        return ""


def argument(argv, *flags):
    for index, value in enumerate(argv):
        if value in flags and index + 1 < len(argv):
            return argv[index + 1]
        for flag in flags:
            if value.startswith(flag + "="):
                return value[len(flag) + 1 :]
    return None


def connection_matches(line, pid, port, bind):
    if re.search(rf"pid={pid}(?:,|\))", line):
        return True
    parts = line.split()
    index = 2 if parts and parts[0].isdigit() else 3
    if len(parts) <= index:
        return False
    host, _, local_port = parts[index].rpartition(":")
    return local_port == str(port) and (
        bind in {"0.0.0.0", "::", "*"} or host.strip("[]") == bind
    )


def parse_discovery(text, address):
    boot, uid, processes, listeners, connections = None, None, [], [], []
    for line in text.splitlines():
        fields = line.split("\t")
        if fields[0] == "boot":
            boot = fields[1]
        elif fields[0] == "uid":
            uid = int(fields[1])
        elif fields[0] == "listen":
            listeners.append(fields[1])
        elif fields[0] == "connection":
            connections.append(fields[1])
        elif fields[0] == "process" and len(fields) == 9:
            pid, start, owner, rss, exe, digest, group, args = fields[1:]
            if not pid.isdigit() or not owner.isdigit():
                continue
            argv = decode(args).rstrip("\0").split("\0")
            cgroup = decode(group)
            units = re.findall(
                r"/([A-Za-z0-9][A-Za-z0-9_.@-]*\.service)(?:/|$)", cgroup.strip()
            )
            user_unit = (
                "user@" in cgroup
                and bool(units)
                and not units[-1].startswith("user@")
                and units[-1] != "ssh.service"
            )
            own_listeners = [
                s for s in listeners if re.search(rf"pid={pid}(?:,|\))", s)
            ]
            # Listener lines arrive before process records in the fixed script.
            endpoints = []
            for listener in own_listeners:
                parts = listener.split()
                if len(parts) < 4:
                    continue
                host, _, port = parts[3].rpartition(":")
                if port.isdigit():
                    endpoints.append((host.strip("[]"), int(port)))
            if not endpoints:
                port = argument(argv, "--port", "-p")
                if port and port.isdigit():
                    endpoints = [
                        (argument(argv, "--host", "-H") or "127.0.0.1", int(port))
                    ]
            for bind, port in endpoints[:4]:
                if not 1024 <= port <= 65535:
                    continue
                devices = argument(argv, "--device", "-d")
                processes.append(
                    {
                        "process_identity": {
                            "pid": int(pid),
                            "start_time": start,
                            "boot_id": boot,
                        },
                        "uid": int(owner),
                        "owned_by_login": int(owner) == uid,
                        "rss_bytes": int(rss or 0),
                        "executable_path": decode(exe),
                        "binary_sha256": digest,
                        "service_name": units[-1] if user_unit else None,
                        "service_scope": "user"
                        if user_unit
                        else (
                            "system"
                            if units and "system.slice" in cgroup
                            else "process"
                        ),
                        "bind_address": bind,
                        "port": port,
                        "reachable": bind in {address, "0.0.0.0", "*", "::"},
                        "device_names": devices.split(",") if devices else None,
                        "client_count": sum(
                            connection_matches(s, pid, port, bind) for s in connections
                        ),
                    }
                )
    all_ports = set()
    for line in listeners:
        parts = line.split()
        if len(parts) >= 4:
            value = parts[3].rpartition(":")[2]
            if value.isdigit():
                all_ports.add(int(value))
    return {"processes": processes[:16], "listening_ports": sorted(all_ports)}
