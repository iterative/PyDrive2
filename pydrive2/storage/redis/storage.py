from oauth2client import client
from redis import Redis


class RedisStorage(client.Storage):
    def __init__(self, redis: Redis, key: str = "pydrive_oauth"):
        super(RedisStorage, self).__init__(lock=redis.lock(key + '_lock'))
        self.redis = redis
        self.key = key

    def acquire_lock(self):
        self._lock.acquire(blocking=True)

    def release_lock(self):
        self._lock.release()

    def locked_get(self):
        serialized = self.redis.get(self.key)

        credentials = client.OAuth2Credentials.from_json(serialized)
        credentials.set_store(self)

        return credentials

    def locked_put(self, credentials: client.Credentials):
        serialized = credentials.to_json()
        self.redis.set(self.key, serialized)

    def locked_delete(self):
        self.redis.delete(self.key)
