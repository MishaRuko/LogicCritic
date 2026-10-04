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
