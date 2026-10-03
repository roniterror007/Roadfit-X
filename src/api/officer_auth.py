"""Local officer password hashing and expiring, revocable bearer sessions."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


class OfficerAuth:
    def __init__(self, directory, clock=time.time):
        self.directory = Path(directory)
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.attempts = {}
        self._credentials = None

    def credentials(self):
        with self.lock:
            if self._credentials is not None:
                return self._credentials
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / 'officer.json'
            if path.exists():
                self._credentials = json.loads(path.read_text(encoding='utf-8'))
            else:
                password = secrets.token_urlsafe(18)
                salt = secrets.token_hex(16)
                self._credentials = {'username': 'officer', 'salt': salt,
                                     'hash': self.password_hash(password, salt)}
                path.write_text(json.dumps(self._credentials, indent=2), encoding='utf-8')
                # The user can open this ignored local file to sign into the demo.
                (self.directory / 'officer-login.txt').write_text(
                    'Local RoadFit officer account\nUsername: officer\nPassword: '+password+
                    '\nGenerated locally. Keep this file private. Sessions expire after one hour.\n', encoding='utf-8')
            return self._credentials

    @staticmethod
    def password_hash(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()

    def login(self, username, password, address):
        credentials = self.credentials()
        with self.lock:
            now = self.clock()
            recent = [t for t in self.attempts.get(address, []) if t > now-60]
            if len(recent) >= 10:
                raise HTTPException(429, 'Too many login attempts. Retry in one minute.')
            self.attempts[address] = recent+[now]
            valid = hmac.compare_digest(username.encode(), credentials['username'].encode())
            valid &= hmac.compare_digest(self.password_hash(password, credentials['salt']), credentials['hash'])
            if not valid:
                raise HTTPException(401, 'Invalid officer credentials.')
            self.attempts.pop(address, None)
            self.sessions = {t: expiry for t, expiry in self.sessions.items() if expiry > now}
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now+3600
            return {'access_token': token, 'token_type': 'bearer', 'expires_in': 3600}

    def validate(self, token):
        with self.lock:
            if not token or self.sessions.get(token, 0.) <= self.clock():
                raise HTTPException(401, 'Officer login required or session expired.')
        return token

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)


AUTH = OfficerAuth(Path(__file__).resolve().parents[2] / '.local')
BEARER = HTTPBearer(auto_error=False)


def require_officer(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER)):
    return AUTH.validate(credentials.credentials if credentials and credentials.scheme.lower() == 'bearer' else None)


if __name__ == '__main__':
    AUTH.credentials()
    print(f'Local officer credentials: {AUTH.directory / "officer-login.txt"}')
