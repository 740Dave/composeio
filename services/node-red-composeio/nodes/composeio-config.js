'use strict';

const path = require('path');
const messageBuilder = require('../lib/message-builder');
const yamlParser = require('../lib/yaml-parser');
const stateEngines = require('../lib/state-engines');

module.exports = function (RED) {
    function ComposeIOConfigNode(config) {
        RED.nodes.createNode(this, config);

        const node = this;
        node.name = config.name || 'ComposeIO Bus';
        node.clientId = config.clientId || ('nodered-' + Math.random().toString(16).slice(2, 8));
        node.scope = config.scope || 'global';
        node.configPath = config.configPath || '';
        node.busStorageUrl = config.busStorageUrl || '';

        // Namespace registry
        node.registry = {};

        // User tracking (child nodes)
        node.users = {};

        // Sequence counter
        node.sequenceCounter = 0;

        // Reference to the mqtt-broker config node
        node.brokerNodeId = config.broker;
        node.brokerNode = null;

        // Internal MQTT subscriptions tracking
        node._internalSubs = [];
        node._brokerConnected = false;

        // Interval handles for heartbeat and discovery
        node._heartbeatInterval = null;
        node._discoveryInterval = null;
        node._heartbeatSeq = 0;

        /**
         * Load namespace definitions from a .compose.yaml file.
         */
        node.loadConfig = function () {
            if (!node.configPath) return;
            try {
                const namespaces = yamlParser.parseConfigFile(node.configPath);
                for (const ns of namespaces) {
                    node.registry[ns.name] = ns;
                }
                node.log(`Loaded ${namespaces.length} namespaces from ${node.configPath}`);
            } catch (err) {
                node.warn(`Failed to load config from ${node.configPath}: ${err.message}`);
            }
        };

        /**
         * Get a namespace definition from the registry.
         */
        node.getNamespace = function (name) {
            return node.registry[name] || null;
        };

        /**
         * Update a namespace's last value in the registry.
         */
        node.updateLastValue = function (name, value) {
            if (!node.registry[name]) {
                node.registry[name] = {
                    name: name,
                    label: name,
                    stateEngine: null,
                    mode: 'state',
                    sources: [],
                    lastValue: value,
                    lastUpdated: Math.floor(Date.now() / 1000)
                };
            } else {
                node.registry[name].lastValue = value;
                node.registry[name].lastUpdated = Math.floor(Date.now() / 1000);
            }
        };

        /**
         * Get the next sequence number.
         */
        node.nextSequence = function () {
            node.sequenceCounter = (node.sequenceCounter + 1) % 65536;
            return node.sequenceCounter;
        };

        /**
         * Subscribe to an MQTT topic via the broker node.
         */
        node.subscribeMqtt = function (topic, qos, callback) {
            if (!node.brokerNode) return;
            node.brokerNode.subscribe(topic, qos, function (topicReceived, payload, packet) {
                callback(topicReceived, payload, packet);
            }, node.id);
            node._internalSubs.push(topic);
        };

        /**
         * Unsubscribe from an MQTT topic via the broker node.
         * Public API for child nodes to cleanly unsubscribe.
         */
        node.unsubscribeMqtt = function (topic) {
            if (!node.brokerNode) return;
            node.brokerNode.unsubscribe(topic, node.id);
            var idx = node._internalSubs.indexOf(topic);
            if (idx !== -1) {
                node._internalSubs.splice(idx, 1);
            }
        };

        /**
         * Publish to an MQTT topic via the broker node.
         */
        node.publishMqtt = function (topic, payload, qos, retain) {
            if (!node.brokerNode) return;
            node.brokerNode.publish({
                topic: topic,
                payload: (typeof payload === 'string') ? payload : JSON.stringify(payload),
                qos: qos,
                retain: retain
            }, function (err) {
                if (err) {
                    node.warn('MQTT publish error: ' + err.message);
                }
            });
        };

        /**
         * Handle discovery messages from the bus.
         */
        node._handleDiscovery = function (topic, payload) {
            try {
                const msg = JSON.parse(payload.toString());
                if (msg.val && msg.val.namespaces) {
                    // Merge published namespaces
                    const pubNs = msg.val.namespaces.publish || [];
                    const subNs = msg.val.namespaces.subscribe || [];
                    const allNs = [].concat(pubNs, subNs);
                    for (const nsName of allNs) {
                        if (!node.registry[nsName]) {
                            node.registry[nsName] = {
                                name: nsName,
                                label: nsName,
                                stateEngine: null,
                                mode: 'state',
                                sources: msg.src ? [msg.src] : [],
                                lastValue: null,
                                lastUpdated: null
                            };
                        } else if (msg.src && node.registry[nsName].sources.indexOf(msg.src) === -1) {
                            node.registry[nsName].sources.push(msg.src);
                        }
                    }
                }
            } catch (e) {
                // Ignore malformed discovery messages
            }
        };

        /**
         * Publish this Node-RED instance's discovery announcement.
         */
        node._publishDiscovery = function () {
            // Collect subscribe/publish namespaces from registered child nodes
            const publishNs = [];
            const subscribeNs = [];
            for (const userId in node.users) {
                const child = node.users[userId];
                if (child._composeioType === 'in') {
                    subscribeNs.push(child._composeioNamespace || '');
                } else if (child._composeioType === 'out') {
                    publishNs.push(child._composeioNamespace || '');
                }
            }

            const discoveryMsg = messageBuilder.buildMessage({
                namespace: '_bus/discovery',
                type: 'discovery',
                value: {
                    module_id: node.clientId,
                    role: 'flow_engine',
                    hw: 'node-red',
                    fw_ver: '1.0.0',
                    namespaces: {
                        publish: publishNs.filter(Boolean),
                        subscribe: subscribeNs.filter(Boolean)
                    },
                    capabilities: ['flow_engine', 'http', 'dashboard', 'automation']
                },
                source: node.clientId,
                scope: 'global',
                sequence: node.nextSequence()
            });

            const topic = messageBuilder.buildTopic('global', '_bus/discovery');
            const qosRetain = messageBuilder.getQosRetain('discovery');
            node.publishMqtt(topic, discoveryMsg, qosRetain.qos, qosRetain.retain);
        };

        /**
         * Set up internal bus subscriptions after broker connection.
         */
        node._setupBusSubscriptions = function () {
            // Subscribe to discovery messages on all scopes
            node.subscribeMqtt('composeio/+/_bus/discovery', 1, function (topic, payload) {
                node._handleDiscovery(topic, payload);
            });

            // Subscribe to heartbeat for bus health monitoring
            node.subscribeMqtt('composeio/+/_bus/heartbeat', 0, function (topic, payload) {
                // Update bus health tracking (future enhancement)
            });

            // Publish our discovery announcement
            node._publishDiscovery();
            node._brokerConnected = true;

            // Periodic heartbeat every 30 seconds (CONFIG-01)
            if (node._heartbeatInterval) clearInterval(node._heartbeatInterval);
            node._heartbeatInterval = setInterval(function () {
                node._heartbeatSeq = (node._heartbeatSeq + 1) % 65536;
                const heartbeatMsg = messageBuilder.buildMessage({
                    namespace: '_bus/heartbeat/nodered-' + node.clientId,
                    type: 'heartbeat',
                    value: 'alive',
                    source: 'nodered-' + node.clientId,
                    scope: 'global',
                    sequence: node._heartbeatSeq
                });
                const hbTopic = messageBuilder.buildTopic('global', '_bus/heartbeat/nodered-' + node.clientId);
                const hbQos = messageBuilder.getQosRetain('heartbeat');
                node.publishMqtt(hbTopic, heartbeatMsg, hbQos.qos, hbQos.retain);
            }, 30000);

            // Periodic discovery re-announcement every 60 seconds (CONFIG-02)
            if (node._discoveryInterval) clearInterval(node._discoveryInterval);
            node._discoveryInterval = setInterval(function () {
                node._publishDiscovery();
            }, 60000);
        };

        /**
         * Register a child node (composeio-in, composeio-out, etc.).
         */
        node.register = function (childNode) {
            node.users[childNode.id] = childNode;

            // On first registration, connect to broker
            if (Object.keys(node.users).length === 1) {
                node.brokerNode = RED.nodes.getNode(node.brokerNodeId);
                if (node.brokerNode) {
                    node.brokerNode.register(node);
                    // Set up broker connect callback
                    node.brokerNode.on('connect', function () {
                        node._setupBusSubscriptions();
                    });
                    // If already connected, set up now
                    if (node.brokerNode.connected) {
                        node._setupBusSubscriptions();
                    }
                } else {
                    node.error('No MQTT broker configured');
                }
            }
        };

        /**
         * Deregister a child node.
         */
        node.deregister = function (childNode, done) {
            delete node.users[childNode.id];

            if (Object.keys(node.users).length === 0) {
                // Clear periodic intervals
                if (node._heartbeatInterval) {
                    clearInterval(node._heartbeatInterval);
                    node._heartbeatInterval = null;
                }
                if (node._discoveryInterval) {
                    clearInterval(node._discoveryInterval);
                    node._discoveryInterval = null;
                }
                // Unsubscribe internal subscriptions
                if (node.brokerNode) {
                    for (const topic of node._internalSubs) {
                        node.brokerNode.unsubscribe(topic, node.id);
                    }
                    node._internalSubs = [];
                    node.brokerNode.deregister(node, function () {
                        node.brokerNode = null;
                        node._brokerConnected = false;
                        node.registry = {};
                        if (done) done();
                    });
                } else {
                    if (done) done();
                }
            } else {
                if (done) done();
            }
        };

        // Load static config on startup
        node.loadConfig();

        // Cleanup on close
        node.on('close', function (done) {
            // Clear periodic intervals
            if (node._heartbeatInterval) {
                clearInterval(node._heartbeatInterval);
                node._heartbeatInterval = null;
            }
            if (node._discoveryInterval) {
                clearInterval(node._discoveryInterval);
                node._discoveryInterval = null;
            }

            if (node.brokerNode) {
                for (const topic of node._internalSubs) {
                    node.brokerNode.unsubscribe(topic, node.id);
                }
                node._internalSubs = [];
                node.brokerNode.deregister(node, function () {
                    node.brokerNode = null;
                    node._brokerConnected = false;
                    done();
                });
            } else {
                done();
            }
        });
    }

    RED.nodes.registerType('composeio-config', ComposeIOConfigNode);
};
