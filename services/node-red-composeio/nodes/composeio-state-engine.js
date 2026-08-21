'use strict';

const stateEngines = require('../lib/state-engines');

module.exports = function (RED) {
    function ComposeIOStateEngineNode(config) {
        RED.nodes.createNode(this, config);

        const node = this;
        node.configNodeId = config.config;
        node.namespace = config.namespace || '';
        node.engineOverride = config.engineOverride || null;

        // Get the config node
        node.composeioConfig = RED.nodes.getNode(node.configNodeId);

        if (!node.composeioConfig) {
            node.error('Missing ComposeIO configuration');
            node.status({ fill: 'red', shape: 'ring', text: 'no config' });
            return;
        }

        // Register with config node
        node.composeioConfig.register(node);

        node.status({ fill: 'green', shape: 'ring', text: 'ready' });

        /**
         * Resolve the state engine for the target namespace.
         */
        function getEngine(nsName) {
            // Override takes priority
            if (node.engineOverride) {
                return stateEngines.resolveEngine(node.engineOverride);
            }

            // Look up from registry
            if (nsName) {
                const nsInfo = node.composeioConfig.getNamespace(nsName);
                if (nsInfo && nsInfo.stateEngine) {
                    return nsInfo.stateEngine;
                }
            }

            return null;
        }

        node.on('input', function (msg, send, done) {
            send = send || function () { node.send.apply(node, arguments); };
            done = done || function (err) { if (err) node.error(err, msg); };

            try {
                // Determine target namespace
                let targetNs = node.namespace;
                if (!targetNs && msg.composeio && msg.composeio.namespace) {
                    targetNs = msg.composeio.namespace;
                }

                // Get engine
                const engine = getEngine(targetNs);

                if (!engine) {
                    // No engine found - pass through on output 1 (valid)
                    node.status({ fill: 'yellow', shape: 'ring', text: 'no engine' });
                    send([msg, null]);
                    done();
                    return;
                }

                // Handle __advance special value
                if (msg.payload === '__advance') {
                    // Get current value from registry
                    let currentValue = engine.default;
                    if (targetNs) {
                        const nsInfo = node.composeioConfig.getNamespace(targetNs);
                        if (nsInfo && nsInfo.lastValue !== null && nsInfo.lastValue !== undefined) {
                            currentValue = nsInfo.lastValue;
                        }
                    }

                    // Advance to next value
                    const nextValue = stateEngines.advance(currentValue, engine);
                    msg.payload = nextValue;

                    // Enrich with stateEngine info
                    if (!msg.composeio) msg.composeio = {};
                    msg.composeio.stateEngine = {
                        type: engine.type,
                        values: engine.values,
                        previousValue: currentValue,
                        advancedTo: nextValue
                    };

                    node.status({
                        fill: 'green',
                        shape: 'dot',
                        text: String(currentValue) + ' -> ' + String(nextValue)
                    });

                    send([msg, null]);
                    done();
                    return;
                }

                // Validate
                const result = stateEngines.validate(msg.payload, engine);

                // Enrich msg.composeio.stateEngine
                if (!msg.composeio) msg.composeio = {};
                msg.composeio.stateEngine = {
                    type: engine.type,
                    values: engine.values
                };

                if (result.valid) {
                    node.status({
                        fill: 'green',
                        shape: 'dot',
                        text: String(msg.payload).slice(0, 15)
                    });
                    send([msg, null]);
                } else {
                    node.status({
                        fill: 'red',
                        shape: 'dot',
                        text: 'invalid: ' + String(msg.payload).slice(0, 10)
                    });
                    msg.error = {
                        message: result.error,
                        validValues: result.validValues
                    };
                    send([null, msg]);
                }

                done();
            } catch (err) {
                node.status({ fill: 'red', shape: 'dot', text: 'error' });
                done(err);
            }
        });

        node.on('close', function (done) {
            node.composeioConfig.deregister(node, function () {
                done();
            });
        });
    }

    RED.nodes.registerType('composeio-state-engine', ComposeIOStateEngineNode);
};
