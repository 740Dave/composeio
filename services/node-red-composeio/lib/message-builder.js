'use strict';

/**
 * ComposeIO message construction utility.
 * Builds 7-field bus messages per spec 01-composeio-bus-protocol.smd.
 */

/**
 * Build a ComposeIO bus message.
 * @param {object} opts
 * @param {string} opts.namespace - Namespace (ns field)
 * @param {string} opts.type - Message type: "state", "event", "log_entry", "discovery", "heartbeat"
 * @param {*} opts.value - The val field
 * @param {string} opts.source - Source module ID (src field)
 * @param {string} [opts.scope="global"] - Scope
 * @param {number} [opts.sequence=0] - Sequence number
 * @param {number} [opts.ttl] - Optional TTL
 * @param {string} [opts.priority] - Optional priority
 * @param {number} [opts.correlationId] - Optional correlation ID
 * @returns {object} ComposeIO bus message
 */
function buildMessage(opts) {
    const msg = {
        ns: opts.namespace,
        type: opts.type || 'state',
        val: opts.value,
        src: opts.source,
        ts: Math.floor(Date.now() / 1000),
        scope: opts.scope || 'global',
        seq: opts.sequence || 0
    };

    if (opts.ttl !== undefined) {
        msg.ttl = opts.ttl;
    }
    if (opts.priority !== undefined) {
        msg.pri = opts.priority;
    }
    if (opts.correlationId !== undefined) {
        msg.cid = opts.correlationId;
    }

    return msg;
}

/**
 * Build a ComposeIO bus message and serialize to JSON string.
 * Enforces BP-MSG-SIZE: messages must not exceed 256 bytes.
 * @param {object} opts - Same options as buildMessage
 * @returns {string} JSON string of the message
 * @throws {Error} If serialized message exceeds 256 bytes
 */
function buildMessageJson(opts) {
    const msg = buildMessage(opts);
    const json = JSON.stringify(msg);
    const byteLen = Buffer.byteLength(json, 'utf8');
    if (byteLen > 256) {
        throw new Error(
            'BP-MSG-SIZE violation: message is ' + byteLen +
            ' bytes, max 256. Namespace: ' + opts.namespace
        );
    }
    return json;
}

/**
 * Build an MQTT topic from scope and namespace.
 * @param {string} scope - "global", "local", "remote", or "auto"
 * @param {string} namespace - ComposeIO namespace
 * @returns {string} MQTT topic
 */
function buildTopic(scope, namespace) {
    return `composeio/${scope}/${namespace}`;
}

/**
 * Parse a ComposeIO MQTT topic.
 * @param {string} topic - e.g. "composeio/global/ftl_arm"
 * @returns {{ scope: string, namespace: string } | null}
 */
function parseTopic(topic) {
    const parts = topic.split('/');
    if (parts.length < 3 || parts[0] !== 'composeio') {
        return null;
    }
    return {
        scope: parts[1],
        namespace: parts.slice(2).join('/')
    };
}

/**
 * Get QoS and retain settings for a message type.
 * Per BP-MQTT-QOS in the bus protocol spec.
 * @param {string} messageType
 * @returns {{ qos: number, retain: boolean }}
 */
function getQosRetain(messageType) {
    switch (messageType) {
        case 'state':
            return { qos: 1, retain: true };
        case 'event':
            return { qos: 0, retain: false };
        case 'log_entry':
            return { qos: 1, retain: false };
        case 'discovery':
            return { qos: 1, retain: true };
        case 'heartbeat':
            return { qos: 0, retain: false };
        default:
            return { qos: 0, retain: false };
    }
}

/**
 * Check if a namespace is a reserved _bus/ infrastructure namespace.
 * @param {string} namespace
 * @returns {boolean}
 */
function isBusNamespace(namespace) {
    return namespace.startsWith('_bus/') || namespace === '_bus';
}

module.exports = {
    buildMessage,
    buildMessageJson,
    buildTopic,
    parseTopic,
    getQosRetain,
    isBusNamespace
};
