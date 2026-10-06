"""A minimal ELM327 over TCP for tests: answers AT commands and replays
recorded responses keyed by (request header, request)."""

import socketserver
import threading


class FakeElm:
    def __init__(self, responses, version="ELM327 v2.2", volts="13.8V"):
        self.responses = responses  # {(header, "22202A"): "frames\nframes"}
        self.version, self.volts = version, volts
        self.requests = []
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                priority, header, buf = "18", "", b""
                while True:
                    data = self.request.recv(1024)
                    if not data:
                        return
                    buf += data
                    while b"\r" in buf:
                        line, buf = buf.split(b"\r", 1)
                        cmd = line.decode().strip().upper().replace(" ", "")
                        if not cmd:
                            continue
                        if cmd.startswith("ATCP"):
                            priority = cmd[4:]
                            reply = "OK"
                        elif cmd.startswith("ATSH"):
                            header = cmd[4:]
                            reply = "OK"
                        elif cmd in ("ATZ", "ATI"):
                            reply = outer.version
                        elif cmd == "ATRV":
                            reply = outer.volts
                        elif cmd.startswith("AT"):
                            reply = "OK"
                        else:
                            full = priority + header if len(header) == 6 \
                                else header
                            outer.requests.append((full, cmd))
                            reply = outer.responses.get((full, cmd),
                                                        "NO DATA")
                        out = reply.replace("\n", "\r") + "\r\r>"
                        self.request.sendall(out.encode())

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0),
                                                      Handler)
        self.server.daemon_threads = True
        self.url = "socket://127.0.0.1:%d" % self.server.server_address[1]

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False
