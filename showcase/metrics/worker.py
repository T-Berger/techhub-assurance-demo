"""Native metric worker with a socket-only relay for local presigned MinIO URLs."""
import os
import socket
import socketserver
import threading

from techhub_privacy_metrics.handler import handle


class Bridge(socketserver.BaseRequestHandler):
    def handle(self):
        target = (os.environ.get("TECHHUB_MINIO_HOST", "host.docker.internal"), int(os.environ.get("TECHHUB_MINIO_PORT", "19010")))
        with socket.create_connection(target, timeout=30) as upstream:
            self.request.settimeout(None)
            upstream.settimeout(None)

            def copy(source, destination):
                try:
                    while chunk := source.recv(65536):
                        destination.sendall(chunk)
                except OSError:
                    pass
                finally:
                    try:
                        destination.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass

            thread = threading.Thread(target=copy, args=(self.request, upstream), daemon=True)
            thread.start()
            copy(upstream, self.request)
            thread.join(timeout=5)


class Relay(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


relay = Relay(("127.0.0.1", int(os.environ.get("TECHHUB_MINIO_PORT", "19010"))), Bridge)
threading.Thread(target=relay.serve_forever, daemon=True).start()
