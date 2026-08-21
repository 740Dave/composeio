'use strict';

const messageBuilder = require('../lib/message-builder');
const stateEngines = require('../lib/state-engines');

module.exports = function (RED) {
    function ComposeIOOutNode(config) {
        RED.nodes.createNode(this, config);

        const node = this;
        node.configNodeId = config.config;
        node.namespace = config.namespace || '';
        node.messageType = config.messageType || 'state';
        node.scope = config.scope || 'global';
        node.source = config.source || '';
        node.validate = config.validate !== false; // default true

        node._composeioType = 'out';
        node._composeioNamespace = node.namespace;

        // Get the config node
        node.composeioConfig = RED.nodes.getNode(node.configNodeId);

        if (!node.composeioConfig) {
            node.error('Missing ComposeIO configuration');
            node.status({ fill: 'red', shape: 'ring', text: 'no config' });
            return;
        }

        // Register with config node
        node.composeioConfig.register(node);

        node.status({ fill: 'green', shape: 'ring', text: 'connected' });

        node.on('input', function (msg, send, done) {
            send = send || function () { node.send.apply(node, arguments); };
            done = done || function (err) { if (err) node.error(err, msg); };

            try {
                // Determine target namespace
                let targetNs = node.namespace;
                if (!targetNs && msg.composeio && msg.composeio.namespace) {
                    targetNs = msg.composeio.namespace;
                }
                if (!targetNs && msg.topic) {
                    const parsed = messageBuilder.parseTopic(msg.topic);
                    if (parsed) {
                        targetNs = parsed.namespace;
                    }
                }

                if (!targetNs) {
                    node.status({ fill: 'red', shape: 'dot', text: 'no namespace' });
                    done(new Error('No target namespace configured or in message'));
                    return;
                }

                // Block publishing to _bus/ namespaces
                if (messageBuilder.isBusNamespace(targetNs)) {
                    node.status({ fill: 'red', shape: 'dot', text: 'blocked: _bus/' });
                    msg.error = {
                        message: `Cannot publish to reserved _bus/ namespace: ${targetNs}`
                    };
                    send([null, msg]);
                    done();
                    return;
                }

                // Determine message type
                let msgType = node.messageType;
                if (msg.composeio && msg.composeio.type) {
                    msgType = msg.composeio.type;
                }

                // Determine scope
                let scope = node.scope;
                if (msg.composeio && msg.composeio.scope) {
                    scope = msg.composeio.scope;
                }

                // Determine source
                let source = node.source || node.composeioConfig.clientId;
                if (msg.composeio && msg.composeio.source) {
                    source = msg.composeio.source;
                }

                // Validate against state engine if enabled
                if (node.validate && msgType === 'state') {
                    const nsInfo = node.composeioConfig.getNamespace(targetNs);
                    if (nsInfo && nsInfo.stateEngine) {
                        const engine = nsInfo.stateEngine;
                        const result = stateEngines.validate(msg.payload, engine);
                        if (!result.valid) {
                            node.status({
                                fill: 'red',
                                shape: 'dot',
                                text: 'invalid: ' + String(msg.payload).slice(0, 10)
                            });
                            msg.error = {
                                message: `Invalid value '${msg.payload}' for state engine ${engine.type} on namespace ${targetNs}`,
                                validValues: result.validValues
                            };
                            send([null, msg]);
                            done();
                            return;
                        }
                    }
                }

                // Build the ComposeIO message with size check (BP-MSG-SIZE: max 256 bytes)
                let busMsgJson;
                try {
                    busMsgJson = messageBuilder.buildMessageJson({
                        namespace: targetNs,
                        type: msgType,
                        value: msg.payload,
                        source: source,
                        scope: scope,
                        sequence: node.composeioConfig.nextSequence()
                    });
                } catch (sizeErr) {
                    node.status({ fill: 'red', shape: 'dot', text: 'too large' });
                    msg.error = { message: sizeErr.message };
                    send([null, msg]);
                    done();
                    return;
                }

                // Get QoS and retain settings
                const qosRetain = messageBuilder.getQosRetain(msgType);

                // Build MQTT topic
                const topic = messageBuilder.buildTopic(scope, targetNs);

                // Publish (busMsgJson is already a string)
                node.composeioConfig.publishMqtt(topic, busMsgJson, qosRetain.qos, qosRetain.retain);

                // Update status
                const displayVal = (typeof msg.payload === 'object')
                    ? JSON.stringify(msg.payload).slice(0, 12)
                    : String(msg.payload);
                node.status({
                    fill: 'green',
                    shape: 'dot',
                    text: '-> ' + displayVal
                });

                // Send on output 1 (valid/published)
                send([msg, null]);
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

    RED.nodes.registerType('composeio-out', ComposeIOOutNode);
};
