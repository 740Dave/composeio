// bus-events.js — ComposeApp web bus bridge (browser side)
// Connects to the SSE endpoint and dispatches bus events to registered handlers.
// No frameworks. Progressive enhancement — pages work without this.
(function() {
    var source = new EventSource('/events');
    var handlers = {};

    window.busEvents = {
        // Register a handler for a namespace.
        // Supports exact match: busEvents.on('_app/board/tasks', fn)
        // Supports wildcard:    busEvents.on('_app/board/*', fn)
        on: function(namespace, callback) {
            if (!handlers[namespace]) handlers[namespace] = [];
            handlers[namespace].push(callback);
        }
    };

    source.addEventListener('bus', function(e) {
        var event = JSON.parse(e.data);
        var ns = event.namespace;

        // Exact match
        if (handlers[ns]) {
            handlers[ns].forEach(function(fn) { fn(event); });
        }

        // Wildcard match: handlers registered with trailing /*
        var keys = Object.keys(handlers);
        for (var i = 0; i < keys.length; i++) {
            var pattern = keys[i];
            if (pattern.endsWith('/*') && ns.startsWith(pattern.slice(0, -1))) {
                handlers[pattern].forEach(function(fn) { fn(event); });
            }
        }
    });

    source.onerror = function() {
        // SSE auto-reconnects. No action needed.
    };
})();
