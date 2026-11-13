#!/usr/bin/env python3
"""websockify with pre-VNC throttling, Origin checks and connection expiry.

Uses websockify's existing transport implementation; no additional resident
service or desktop is needed. Requires the Ubuntu python3-websockify package.
"""
import argparse
import json
import signal
import sqlite3
import sys
from pathlib import Path

from websockify.auth_plugins import AuthenticationError
from websockify.websocketproxy import ProxyRequestHandler, WebSocketProxy

from gateway_security import AttemptLimiter, allowed_origin


class GuardedHandler(ProxyRequestHandler):
    def deny(self, code, message):
        error = AuthenticationError(response_code=code, response_msg=message,
                                    response_headers={'Retry-After': '60'} if code == 429 else {})
        self.send_auth_error(error)
        raise error

    def validate_connection(self):
        if self.path != '/websockify':
            self.deny(404, 'Unknown WebSocket endpoint')
        if not allowed_origin(self.headers, self.server.allowed_origins):
            self.deny(403, 'Origin not allowed')
        try:
            admitted = self.server.attempt_limiter.admit(self.client_address[0])
        except (OSError, sqlite3.Error):
            # Fail closed if the shared limiter cannot be read/written.
            self.deny(503, 'Connection limiter unavailable')
        if not admitted:
            self.log_message('VNC connection rate limit exceeded')
            self.deny(429, 'Too many connection attempts; wait 60 seconds')
        super().validate_connection()

    def new_websocket_client(self):
        # websockify forks a worker per request, so this timer is per connection.
        # It also expires silent, inactive or otherwise stalled connections.
        def expire(signum, frame):
            raise self.CClose(1008, 'Remote session expired; reconnect to authenticate again')

        previous = signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, self.server.session_seconds)
        try:
            super().new_websocket_client()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        super().end_headers()

    def log_request(self, code='-', size='-'):
        # Don't record URLs/query strings containing credentials or tokens.
        self.log_message('HTTP %s %s', self.command, code)


def create_server(config, inetd=False, idle_timeout=30):
    security = config['connection_security']
    seconds = int(security['session_seconds'])
    if not 10 <= seconds <= 86400:
        raise ValueError('원격 세션 시간은 10~86400초여야 합니다.')
    server = WebSocketProxy(
        RequestHandlerClass=GuardedHandler,
        listen_fd=0 if inetd else None,
        listen_host=config['listen_address'], listen_port=int(config['web_port']),
        target_host='127.0.0.1', target_port=int(config['vnc_port']),
        web=config['web_root'], file_only=True, heartbeat=20,
        idle_timeout=idle_timeout if inetd else 0,
        cert=config.get('cert', ''), key=config.get('key') or None,
        ssl_only=bool(config.get('cert')),
    )
    server.allowed_origins = set(security['allowed_origins'])
    server.session_seconds = seconds
    server.attempt_limiter = AttemptLimiter(
        security['database'], per_ip=5, total=30, window=60)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--inetd', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    create_server(config, args.inetd).start_server()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
