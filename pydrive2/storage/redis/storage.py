from oauth2client import client
from redis import Redis


class RedisStorage(client.Storage):
    def __init__(self, redis: Redis, key: str = "pydrive_oauth"):
        super(RedisStorage, self).__init__()
        self.redis = redis
        self.key = key
        self._lock = redis.lock(key + '_lock')

    def get(self):
        return self.locked_get()

    def put(self, credentials: client.Credentials):
        self.locked_put(credentials)

    def delete(self):
        self.delete()

    # Locked operations

    def acquire_lock(self):
        self._lock.acquire(blocking=True)

    def release_lock(self):
        self._lock.release()

    def locked_get(self):
        self.acquire_lock()

        try:
            serialized = self.redis.get(self.key)

            credentials = client.OAuth2Credentials.from_json(serialized)
            credentials.set_store(self)

            return credentials
        finally:
            self.release_lock()

    def locked_put(self, credentials: client.Credentials):
        self.acquire_lock()
        try:
            serialized = credentials.to_json()
            self.redis.set(self.key, serialized)
        finally:
            self.release_lock()

    def locked_delete(self):
        self.acquire_lock()
        try:
            self.redis.delete(self.key)
        finally:
            self.release_lock()
