# Traceability: Design Principles

*Auto-generated traceability matrix. Regenerated on every build.*

| Principle | Derived Constraints | Anti-patterns | Specs Referencing |
|---|---|---|---|
| [DP-CIO-1](../../spec/00-composeio-design-principles/#dp-cio-1-adding-hardware-is-configuration-not-recoding) | [CXC-DUAL-BUS](../../composer/01-composer-architecture/#1-dual-bus-topology-cxc-dual-bus), [CXC-R-DP-3](../../composer/12-composeux-render-engine/#cxc-r-dp-3-configuration-drives-everything), [MR-CONFORMANCE](../../spec/03-composeio-module-roles/#conformance-checklist-mr-conformance), [MR-IMPL-SAFE-MODE](../../spec/03-composeio-module-roles/#mr-impl-safe-mode-safe-mode-behavior), [SD-TRACE-PATTERN](../../spec/11-smd-mkdocs/#sd-trace-pattern-chain-detection) | — | 5 specs |
| [DP-CIO-10](../../spec/00-composeio-design-principles/#dp-cio-10-sensor-to-actuator-bindings-are-first-class) | [NR-PATTERN-BINDING](../../spec/10-composeio-node-red-integration/#91-namespace-to-namespace-binding-nr-pattern-binding) | — | 3 specs |
| [DP-CIO-11](../../spec/00-composeio-design-principles/#dp-cio-11-the-system-degrades-gracefully-not-catastrophically) | [CXC-R-BOOT-FUNGIBLE](../../composer/12-composeux-render-engine/#cxc-r-boot-fungible-hardware-fungibility), [MR-CONTRACT-WATCHDOG](../../spec/03-composeio-module-roles/#mr-contract-watchdog-watchdog-timer), [MR-IMPL-SAFE-MODE](../../spec/03-composeio-module-roles/#mr-impl-safe-mode-safe-mode-behavior), [NR-PATTERN-BINDING](../../spec/10-composeio-node-red-integration/#91-namespace-to-namespace-binding-nr-pattern-binding) | — | 4 specs |
| [DP-CIO-12](../../spec/00-composeio-design-principles/#dp-cio-12-led-behavior-is-declarative-not-procedural) | — | — | — |
| [DP-CIO-13](../../spec/00-composeio-design-principles/#dp-cio-13-buttons-can-be-modal-context-changes-behavior) | — | — | — |
| [DP-CIO-14](../../spec/00-composeio-design-principles/#dp-cio-14-repetition-is-a-design-failure-parameterize-dont-copy-paste) | — | — | 1 specs |
| [DP-CIO-15](../../spec/00-composeio-design-principles/#dp-cio-15-effects-and-animations-are-named-parameterized-primitives) | — | — | — |
| [DP-CIO-16](../../spec/00-composeio-design-principles/#dp-cio-16-control-priority-is-explicit-disabled-manual-auto) | [MR-ROLE-COMPOSITE-DEVICE](../../spec/03-composeio-module-roles/#mr-role-composite-device-role-composite-device-id-0x06), [MR-ROLE-RELAY-CONTROLLER](../../spec/03-composeio-module-roles/#mr-role-relay-controller-role-relay-controller-id-0x02) | — | 1 specs |
| [DP-CIO-17](../../spec/00-composeio-design-principles/#dp-cio-17-the-bus-provides-shared-infrastructure-services) | — | — | — |
| [DP-CIO-18](../../spec/00-composeio-design-principles/#dp-cio-18-large-payloads-are-referenced-not-transmitted) | — | — | — |
| [DP-CIO-19](../../spec/00-composeio-design-principles/#dp-cio-19-state-authority-can-be-local-or-external) | — | — | — |
| [DP-CIO-2](../../spec/00-composeio-design-principles/#dp-cio-2-apis-must-not-be-brittle-pubsub-by-namespace) | — | — | — |
| [DP-CIO-20](../../spec/00-composeio-design-principles/#dp-cio-20-external-services-join-the-bus-through-declarative-adapters) | [NR-PATTERN-HA-BRIDGE](../../spec/10-composeio-node-red-integration/#92-ha-entity-bridge-nr-pattern-ha-bridge) | — | 2 specs |
| [DP-CIO-21](../../spec/00-composeio-design-principles/#dp-cio-21-namespace-binding-can-be-dynamic-selectors-and-polymorphism) | — | — | — |
| [DP-CIO-22](../../spec/00-composeio-design-principles/#dp-cio-22-signal-transforms-are-declarative-threshold-debounce-scale-map) | — | — | — |
| [DP-CIO-23](../../spec/00-composeio-design-principles/#dp-cio-23-the-bus-has-persistent-storage-config-assets-and-state) | [APP-INTEGRATION-PROTOCOL](../../spec/08-composeio-app-framework/#62-relationship-to-01-bus-protocol-app-integration-protocol), [CXC-R-BOOT-FUNGIBLE](../../composer/12-composeux-render-engine/#cxc-r-boot-fungible-hardware-fungibility) | — | 2 specs |
| [DP-CIO-24](../../spec/00-composeio-design-principles/#dp-cio-24-compute-intensive-work-runs-on-a-service-hub-not-edge-devices) | [APP-PROBLEM-GAP](../../spec/08-composeio-app-framework/#11-the-gap-between-workloads-and-applications-app-problem-gap) | — | 1 specs |
| [DP-CIO-25](../../spec/01-amendment-bus-extensions/#dp-cio-25-services-are-bus-participants-not-bus-extensions) | [NR-ARCH-RATIONALE](../../spec/10-composeio-node-red-integration/#12-design-rationale-nr-arch-rationale) | — | 1 specs |
| [DP-CIO-3](../../spec/00-composeio-design-principles/#dp-cio-3-push-behavior-to-the-edge) | [MR-ROLE-COMPOSITE-DEVICE](../../spec/03-composeio-module-roles/#mr-role-composite-device-role-composite-device-id-0x06), [MR-ROLE-FINGERPRINT-GATE](../../spec/03-composeio-module-roles/#mr-role-fingerprint-gate-role-fingerprint-gate-id-0x05), [MR-ROLE-SENSOR-HUB](../../spec/03-composeio-module-roles/#mr-role-sensor-hub-role-sensor-hub-id-0x07), [MR-ROLE-STEPPER-CONTROLLER](../../spec/03-composeio-module-roles/#mr-role-stepper-controller-role-stepper-controller-id-0x03) | — | 1 specs |
| [DP-CIO-4](../../spec/00-composeio-design-principles/#dp-cio-4-separate-input-mechanics-from-state-domain) | — | — | 1 specs |
| [DP-CIO-5](../../spec/00-composeio-design-principles/#dp-cio-5-the-bus-has-a-local-loop-and-a-global-loop) | [SD-PROBLEM](../../spec/11-smd-mkdocs/#sd-problem-the-traversal-gap) | — | 3 specs |
| [DP-CIO-6](../../spec/00-composeio-design-principles/#dp-cio-6-transport-is-an-implementation-detail-not-an-architectural-decision) | [APP-PROBLEM-PLATFORM](../../spec/08-composeio-app-framework/#13-cross-platform-equivalence-app-problem-platform), [CXC-R-DP-2](../../composer/12-composeux-render-engine/#cxc-r-dp-2-rendering-target-is-a-transport-level-concern), [NR-ARCH-RATIONALE](../../spec/10-composeio-node-red-integration/#12-design-rationale-nr-arch-rationale) | — | 5 specs |
| [DP-CIO-7](../../spec/00-composeio-design-principles/#dp-cio-7-configuration-is-the-product-firmware-is-infrastructure) | [CXC-R-DP-3](../../composer/12-composeux-render-engine/#cxc-r-dp-3-configuration-drives-everything), [MR-CONFORMANCE](../../spec/03-composeio-module-roles/#conformance-checklist-mr-conformance), [NR-ARCH-RATIONALE](../../spec/10-composeio-node-red-integration/#12-design-rationale-nr-arch-rationale) | — | 5 specs |
| [DP-CIO-8](../../spec/00-composeio-design-principles/#dp-cio-8-compute-platform-is-irrelevant) | — | — | — |
| [DP-CIO-9](../../spec/00-composeio-design-principles/#dp-cio-9-the-bus-extends-beyond-the-local-network) | — | — | — |
| [DP-CXC-1](../../composer/00-composer-design-principles/#dp-cxc-1-the-composer-is-a-composeio-application) | [CXC-DUAL-BUS](../../composer/01-composer-architecture/#1-dual-bus-topology-cxc-dual-bus) | — | 1 specs |
| [DP-CXC-10](../../composer/00-composer-design-principles/#dp-cxc-10-intelligence-friendly-by-default) | [CXC-INTEL-PRINCIPLE](../../composer/06-composer-intelligence-api/#1-governing-principle-cxc-intel-principle) | — | 1 specs |
| [DP-CXC-2](../../composer/00-composer-design-principles/#dp-cxc-2-visual-layout-is-the-primary-artifact) | — | — | — |
| [DP-CXC-3](../../composer/00-composer-design-principles/#dp-cxc-3-namespace-binding-is-visual-not-textual) | — | — | — |
| [DP-CXC-4](../../composer/00-composer-design-principles/#dp-cxc-4-live-preview-with-real-bus) | [CXC-BUS-DESIGN](../../composer/01-composer-architecture/#cxc-bus-design-design-bus-live), [CXC-PREV-PRINCIPLE](../../composer/05-composer-preview-system/#cxc-prev-principle-core-principle) | — | 2 specs |
| [DP-CXC-5](../../composer/00-composer-design-principles/#dp-cxc-5-decomposition-over-monolith) | [CXC-MOD-ISOLATION](../../composer/01-composer-architecture/#cxc-mod-isolation-module-isolation-contract) | — | 1 specs |
| [DP-CXC-6](../../composer/00-composer-design-principles/#dp-cxc-6-configuration-hierarchy-design-module-widget) | — | — | — |
| [DP-CXC-7](../../composer/00-composer-design-principles/#dp-cxc-7-round-trip-fidelity) | [CXC-RT-RULES](../../composer/04-composer-serialization/#cxc-rt-rules-round-trip-rules) | — | 1 specs |
| [DP-CXC-8](../../composer/00-composer-design-principles/#dp-cxc-8-the-widget-catalog-is-extensible) | — | — | — |
| [DP-CXC-9](../../composer/00-composer-design-principles/#dp-cxc-9-undo-is-structural-not-textual) | — | — | — |
| [DP-NEWS-1](../../spec/06-news-service/#dp-news-1-async-acquisition-sync-consumption) | — | — | — |
| [DP-NEWS-2](../../spec/06-news-service/#dp-news-2-agent-defined-subscriptions-service-curated-delivery) | — | — | — |
| [DP-NEWS-3](../../spec/06-news-service/#dp-news-3-source-agnostic-architecture) | — | — | — |
| [DP-NEWS-4](../../spec/06-news-service/#dp-news-4-summarization-via-claude-api) | — | — | — |
| [DP-NOTIFY-1](../../spec/07-notification-bridge/#dp-notify-1-namespace-driven-not-api-driven) | — | — | — |
| [DP-NOTIFY-2](../../spec/07-notification-bridge/#dp-notify-2-urgent-only-not-a-firehose) | — | — | — |
| [DP-NOTIFY-3](../../spec/07-notification-bridge/#dp-notify-3-transport-pluggable) | — | — | — |

---

*Generated by mkdocs-smd plugin*