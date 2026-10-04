import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
afterEach(cleanup);
class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
vi.stubGlobal('ResizeObserver', ResizeObserverMock);
vi.stubGlobal('matchMedia', vi.fn().mockImplementation(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn() })));
Element.prototype.scrollIntoView = vi.fn();
window.scrollTo = vi.fn();
// Recent Node versions define a global `localStorage` that is unusable without a backing file and
// shadows jsdom's. Give tests a working in-memory Storage in that case.
if (typeof globalThis.localStorage?.clear !== 'function') {
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, String(value)); },
    removeItem: (key: string) => { store.delete(key); },
    clear: () => { store.clear(); },
    key: (index: number) => [...store.keys()][index] ?? null,
    get length() { return store.size; },
  });
}
