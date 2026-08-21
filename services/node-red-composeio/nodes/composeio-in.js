'use strict';

const messageBuilder = require('../lib/message-builder');

module.exports = function (RED) {
    function ComposeIOInNode(config) {
        RED.nodes.createNode(this, config);

        const node = this;
        node.configNodeId = config.config;
        node.namespace = config.namespace || '';
        node.scope = config.scope || 'global';
        node.messageTypes = config.messageTypes || ['state'];
        node.outputFormat = config.outputFormat || 'parsed';
        node.onlyOnChange = config.onlyOnChange !== false; // default true

        node._composeioType = 'in';
        node._composeioNamespace = node.namespace;
        node._lastValue = undefined;

        // Get the config node
        node.composeioConfig = RED.nodes.getNode(node.configNodeId);

        if (!node.composeioConfig) {
            node.error('Missing ComposeIO configuration');
            node.status({ fill: 'red', shape: 'ring', text: 'no config' });
            return;
        }

        // Register with config node
        node.composeioConfig.register(node);

        node.status({ fill: 'yellow', shape: 'ring', text: 'connecting...' });

        /**
         * Build the MQTT topic to subscribe to.
         */
        function buildSubscriptionTopic() {
            const scopePart = (node.scope === 'any') ? '+' : node.scope;
            return `composeio/${scopePart}/${node.namespace}`;
        }

        /**
         * Check if a message type should be passed through.
         */
        function shouldPassType(type) {
            if (node.messageTypes.indexOf('all') !== -1) return true;
            return node.messageTypes.indexOf(type) !== -1;
        }

        /**
         * Handle incoming MQTT messages.
         */
        function handleMessage(topic, payload, packet) {
            let parsed;
            try {
                parsed = JSON.parse(payload.toString());
            } catch (e) {
                node.warn('Failed to parse ComposeIO message: ' + e.message);
                return;
            }

            // Filter by message type
            if (!shouldPassType(parsed.type)) {
                return;
            }

            // onlyOnChange for state messages
            if (node.onlyOnChange && parsed.type === 'state') {
                const newVal = JSON.stringify(parsed.val);
                const oldVal = JSON.stringify(node._lastValue);
                if (newVal === oldVal) {
                    return;
                }
            }
            node._lastValue = parsed.val;

            // Update registry
            const topicParsed = messageBuilder.parseTopic(topic);
            if (topicParsed) {
                node.composeioConfig.updateLastValue(topicParsed.namespace, parsed.val);
            }

            // Build output message
            let msg;
            if (node.outputFormat === 'raw') {
                msg = {
                    topic: topic,
                    payload: payload.toString()
                };
            } else {
                // Look up namespace info from registry
                const nsName = parsed.ns || (topicParsed ? topicParsed.namespace : '');
                const nsInfo = node.composeioConfig.getNamespace(nsName);

                msg = {
                    topic: topic,
                    payload: parsed.val,
                    composeio: {
                        namespace: nsName,
                        type: parsed.type,
                        value: parsed.val,
                        source: parsed.src,
                        timestamp: parsed.ts,
                        scope: parsed.scope,
                        sequence: parsed.seq
                    }
                };

                // Add registry info if available
                if (nsInfo) {
                    msg.composeio.label = nsInfo.label;
                    if (nsInfo.stateEngine) {
                        msg.composeio.stateEngine = nsInfo.stateEngine;
                    }
                }

                // Add optional fields
                if (parsed.ttl !== undefined) msg.composeio.ttl = parsed.ttl;
                if (parsed.pri !== undefined) msg.composeio.priority = parsed.pri;
                if (parsed.cid !== undefined) msg.composeio.correlationId = parsed.cid;
            }

            // Update status display
            const displayVal = (typeof parsed.val === 'object')
                ? JSON.stringify(parsed.val).slice(0, 15)
                : String(parsed.val);
            node.status({ fill: 'green', shape: 'dot', text: displayVal });

            node.send(msg);
        }

        // Subscribe to MQTT via config node
        const subTopic = buildSubscriptionTopic();
        // Determine QoS: state gets QoS 1, events get QoS 0
        const hasStateType = node.messageTypes.indexOf('state') !== -1 || node.messageTypes.indexOf('all') !== -1;
        const subQos = hasStateType ? 1 : 0;

        node.composeioConfig.subscribeMqtt(subTopic, subQos, handleMessage);

        node.status({ fill: 'green', shape: 'ring', text: 'connected' });

        // Cleanup on close
        node.on('close', function (done) {
            if (node.composeioConfig) {
                node.composeioConfig.unsubscribeMqtt(subTopic);
            }
            node.composeioConfig.deregister(node, function () {
                done();
            });
        });
    }

    RED.nodes.registerType('composeio-in', ComposeIOInNode);
};
