'use strict';

/**
 * ComposeIO State Engine validation library.
 * Implements all state engine types from spec 02-composeio-config-schema.smd
 * and the json extension from 01-amendment-bus-extensions.smd.
 */

const BUILTIN_ENGINES = {
    binary: {
        type: 'binary',
        values: ['off', 'on'],
        default: 'off'
    },
    tri_level: {
        type: 'tri_level',
        values: ['off', 'half', 'full'],
        default: 'off'
    },
    stop_light: {
        type: 'stop_light',
        values: ['green', 'yellow', 'red'],
        default: 'green'
    },
    continuous_0_255: {
        type: 'continuous_0_255',
        values: null, // range 0-255
        default: 0
    },
    json: {
        type: 'json',
        values: null,
        default: null
    }
};

/**
 * Resolve a state engine definition from shorthand or expanded syntax.
 * @param {string|object} engineDef - "binary" or { type: "enum", values: [...], default: "..." }
 * @returns {object} Normalized engine definition { type, values, default }
 */
function resolveEngine(engineDef) {
    if (typeof engineDef === 'string') {
        const builtin = BUILTIN_ENGINES[engineDef];
        if (!builtin) {
            return null;
        }
        return Object.assign({}, builtin);
    }
    if (typeof engineDef === 'object' && engineDef !== null) {
        if (engineDef.type && BUILTIN_ENGINES[engineDef.type]) {
            return Object.assign({}, BUILTIN_ENGINES[engineDef.type], engineDef);
        }
        if (engineDef.type === 'enum') {
            return {
                type: 'enum',
                values: engineDef.values || [],
                default: engineDef.default || (engineDef.values ? engineDef.values[0] : null)
            };
        }
        if (engineDef.type === 'json') {
            return Object.assign({}, BUILTIN_ENGINES.json, engineDef);
        }
        return engineDef;
    }
    return null;
}

/**
 * Validate a value against a state engine definition.
 * @param {*} value - The value to validate
 * @param {object} engine - Resolved engine definition
 * @returns {{ valid: boolean, error?: string, validValues?: array }}
 */
function validate(value, engine) {
    if (!engine || !engine.type) {
        return { valid: true }; // No engine = no validation
    }

    switch (engine.type) {
        case 'binary':
        case 'tri_level':
        case 'stop_light': {
            const values = engine.values || BUILTIN_ENGINES[engine.type].values;
            if (values.indexOf(String(value)) === -1) {
                return {
                    valid: false,
                    error: `Invalid value '${value}' for state engine ${engine.type}`,
                    validValues: values
                };
            }
            return { valid: true };
        }

        case 'continuous_0_255': {
            const num = Number(value);
            if (!Number.isInteger(num) || num < 0 || num > 255) {
                return {
                    valid: false,
                    error: `Invalid value '${value}' for state engine continuous_0_255 (must be integer 0-255)`,
                    validValues: '0-255'
                };
            }
            return { valid: true };
        }

        case 'enum': {
            const values = engine.values || [];
            if (values.indexOf(String(value)) === -1) {
                return {
                    valid: false,
                    error: `Invalid value '${value}' for state engine enum`,
                    validValues: values
                };
            }
            return { valid: true };
        }

        case 'json': {
            // Any JSON-serializable value is valid (per BP-EXT-JSON spec)
            try {
                JSON.stringify(value);
                return { valid: true };
            } catch (e) {
                return {
                    valid: false,
                    error: `Invalid value for state engine json: ${e.message}`
                };
            }
        }

        default:
            return { valid: true }; // Unknown engine type = pass through
    }
}

/**
 * Advance to the next value in the state engine's cycle.
 * @param {*} currentValue - The current state value
 * @param {object} engine - Resolved engine definition
 * @returns {*} The next value, or currentValue if no cycle defined
 */
function advance(currentValue, engine) {
    if (!engine || !engine.type) {
        return currentValue;
    }

    switch (engine.type) {
        case 'binary':
        case 'tri_level':
        case 'stop_light':
        case 'enum': {
            const values = engine.values || BUILTIN_ENGINES[engine.type].values;
            if (!values || values.length === 0) {
                return currentValue;
            }
            const currentIdx = values.indexOf(String(currentValue));
            if (currentIdx === -1) {
                // Current value not in cycle; return default
                return engine.default || values[0];
            }
            const nextIdx = (currentIdx + 1) % values.length;
            return values[nextIdx];
        }

        case 'continuous_0_255': {
            // Toggle behavior: cycle through [0, 128, 255]
            const toggleValues = [0, 128, 255];
            const num = Number(currentValue) || 0;
            const idx = toggleValues.indexOf(num);
            if (idx === -1) {
                // Find the nearest toggle value and go to next
                return 128;
            }
            return toggleValues[(idx + 1) % toggleValues.length];
        }

        case 'json':
            // No meaningful "next" for JSON
            return currentValue;

        default:
            return currentValue;
    }
}

module.exports = {
    BUILTIN_ENGINES,
    resolveEngine,
    validate,
    advance
};
