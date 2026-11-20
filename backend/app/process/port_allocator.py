import socket


class PortAllocator:
    def __init__(self, settings):
        self.settings = settings

    def allocate(self, used: set[int]) -> int:
        for port in range(
            self.settings.port_range.start, self.settings.port_range.end + 1
        ):
            if port in used:
                continue
            with socket.socket() as sock:
                try:
                    sock.bind((self.settings.host, port))
                except OSError:
                    continue
                return port
        raise ValueError("No free inference ports in the configured range")
