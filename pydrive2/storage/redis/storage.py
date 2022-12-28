from oauth2client import client
from redis import Redis


class RedisStorage(client.Storage):
    def __init__(self, redis: Redis, key: str = 'pydrive_auth'):
        super(RedisStorage, self).__init__()
        self.redis = redis
        self.key = key

    def get(self):
        serialized = self.redis.get(self.key)

        if serialized is None:
            return None

        credentials = client.OAuth2Credentials.from_json(serialized)
        credentials.set_store(self)

        return credentials

    def put(self, credentials):
        serialized = credentials.to_json()
        self.redis.set(self.key, serialized)

    def delete(self):
        self.redis.delete(self.key)
