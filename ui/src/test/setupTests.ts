import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => cleanup())

// jsdom does not implement scrolling; the block workbench scrolls to a block it has just selected.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
