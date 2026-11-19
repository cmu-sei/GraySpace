import { authHeaders, clearToken, getToken, setToken, withToken } from './auth';

describe('Session token', () => {
  afterEach(() => clearToken());

  it('is stored and sent as a bearer token', () => {
    expect(authHeaders()).toEqual({});
    setToken('  secret  ');
    expect(getToken()).toBe('secret');
    expect(authHeaders()).toEqual({ Authorization: 'Bearer secret' });
  });

  it('is added to websocket URLs', () => {
    expect(withToken('ws://h/ws/console/r?cols=80')).toBe('ws://h/ws/console/r?cols=80');
    setToken('a b');
    expect(withToken('ws://h/ws/console/r?cols=80')).toBe('ws://h/ws/console/r?cols=80&token=a%20b');
    expect(withToken('ws://h/api/v1/events')).toBe('ws://h/api/v1/events?token=a%20b');
  });
});
