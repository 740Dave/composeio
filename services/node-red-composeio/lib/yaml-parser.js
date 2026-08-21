'use strict';

const yaml = require('js-yaml');
const fs = require('fs');
const path = require('path');
const stateEngines = require('./state-engines');

/**
 * Parse a .compose.yaml file and extract namespace definitions.
 * Handles all top-level constructs: module, composite_device, workload, bindings, adapter.
 *
 * @param {string} filePath - Path to .compose.yaml file
 * @returns {object[]} Array of namespace definitions
 */
function parseConfigFile(filePath) {
    const content = fs.readFileSync(path.resolve(filePath), 'utf8');
    return parseConfigYaml(content);
}

/**
 * Parse .compose.yaml content string and extract namespace definitions.
 * @param {string} yamlContent
 * @returns {object[]} Array of namespace definitions
 */
function parseConfigYaml(yamlContent) {
    const doc = yaml.load(yamlContent);
    if (!doc || typeof doc !== 'object') {
        return [];
    }

    const namespaces = [];
    const moduleScope = doc.scope || '';

    // Extract from module config arrays
    const configArrayKeys = ['buttons', 'channels', 'gauges', 'axes', 'encoders', 'leds', 'sensors'];
    for (const key of configArrayKeys) {
        if (Array.isArray(doc[key])) {
            for (const item of doc[key]) {
                const ns = extractNamespaceFromItem(item, moduleScope);
                if (ns) {
                    namespaces.push(ns);
                }
            }
        }
    }

    // Extract from composite_device components
    if (Array.isArray(doc.components)) {
        for (const comp of doc.components) {
            if (Array.isArray(comp.buttons || comp.channels || comp.gauges)) {
                const items = comp.buttons || comp.channels || comp.gauges || [];
                for (const item of items) {
                    const ns = extractNamespaceFromItem(item, moduleScope);
                    if (ns) {
                        namespaces.push(ns);
                    }
                }
            }
        }
    }

    // Extract from explicit namespaces block
    if (Array.isArray(doc.namespaces)) {
        for (const item of doc.namespaces) {
            const ns = extractNamespaceFromItem(item, moduleScope);
            if (ns) {
                namespaces.push(ns);
            }
        }
    }

    return namespaces;
}

/**
 * Extract a namespace definition from a config item.
 * @param {object} item - Config item with namespace/name field
 * @param {string} moduleScope - Module-level scope
 * @returns {object|null} Namespace definition
 */
function extractNamespaceFromItem(item, moduleScope) {
    if (!item) return null;

    const name = item.namespace || item.name;
    if (!name) return null;

    // Resolve scope
    let scope = moduleScope;
    if (item.scope !== undefined) {
        scope = item.scope || '';
    }

    // Resolve state engine
    let engineDef = item.state_engine || item.stateEngine;
    let engine = null;
    if (engineDef) {
        engine = stateEngines.resolveEngine(engineDef);
    }

    return {
        name: name,
        label: item.label || name,
        stateEngine: engine,
        mode: item.mode || 'state',
        scope: scope,
        sources: [],
        lastValue: engine ? engine.default : null,
        lastUpdated: null
    };
}

module.exports = {
    parseConfigFile,
    parseConfigYaml,
    extractNamespaceFromItem
};
