'use strict';

const should = require('should');
const helper = require('node-red-node-test-helper');
const configNode = require('../nodes/composeio-config');
const inNode = require('../nodes/composeio-in');
const outNode = require('../nodes/composeio-out');
const engineNode = require('../nodes/composeio-state-engine');

helper.init(require.resolve('node-red'));

describe('ComposeIO Nodes', function () {

    afterEach(function (done) {
        helper.unload().then(function () {
            done();
        });
    });

    describe('composeio-config', function () {
        it('should load with defaults', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Test Config' }
            ];
            helper.load(configNode, flow, function () {
                var n = helper.getNode('c1');
                n.should.have.property('name', 'Test Config');
                n.should.have.property('registry');
                n.should.have.property('users');
                done();
            });
        });

        it('should generate clientId when not provided', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Test', clientId: '' }
            ];
            helper.load(configNode, flow, function () {
                var n = helper.getNode('c1');
                n.clientId.should.startWith('nodered-');
                done();
            });
        });

        it('should manage sequence counter with wrapping', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Test' }
            ];
            helper.load(configNode, flow, function () {
                var n = helper.getNode('c1');
                var seq1 = n.nextSequence();
                seq1.should.equal(1);
                var seq2 = n.nextSequence();
                seq2.should.equal(2);

                // Test wrapping
                n.sequenceCounter = 65535;
                var seqWrap = n.nextSequence();
                seqWrap.should.equal(0);
                done();
            });
        });

        it('should maintain namespace registry', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Test' }
            ];
            helper.load(configNode, flow, function () {
                var n = helper.getNode('c1');
                should(n.getNamespace('ftl_arm')).be.null();

                n.updateLastValue('ftl_arm', 'on');
                var ns = n.getNamespace('ftl_arm');
                ns.should.have.property('name', 'ftl_arm');
                ns.should.have.property('lastValue', 'on');
                done();
            });
        });
    });

    describe('composeio-state-engine', function () {
        it('should load with defaults', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Config' },
                { id: 'e1', type: 'composeio-state-engine', name: 'Engine',
                  config: 'c1', namespace: 'ftl_arm', wires: [['h1'], ['h2']] },
                { id: 'h1', type: 'helper' },
                { id: 'h2', type: 'helper' }
            ];
            helper.load([configNode, engineNode], flow, function () {
                var n = helper.getNode('e1');
                n.should.have.property('name', 'Engine');
                done();
            });
        });

        it('should validate against engine override', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Config' },
                { id: 'e1', type: 'composeio-state-engine', name: 'Engine',
                  config: 'c1', namespace: '', engineOverride: { type: 'binary' },
                  wires: [['h1'], ['h2']] },
                { id: 'h1', type: 'helper' },
                { id: 'h2', type: 'helper' }
            ];
            helper.load([configNode, engineNode], flow, function () {
                var e = helper.getNode('e1');
                var h1 = helper.getNode('h1');
                var h2 = helper.getNode('h2');

                h1.on('input', function (msg) {
                    msg.payload.should.equal('on');
                    msg.composeio.stateEngine.type.should.equal('binary');
                    done();
                });

                e.receive({ payload: 'on' });
            });
        });

        it('should route invalid values to output 2', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Config' },
                { id: 'e1', type: 'composeio-state-engine', name: 'Engine',
                  config: 'c1', namespace: '', engineOverride: { type: 'binary' },
                  wires: [['h1'], ['h2']] },
                { id: 'h1', type: 'helper' },
                { id: 'h2', type: 'helper' }
            ];
            helper.load([configNode, engineNode], flow, function () {
                var e = helper.getNode('e1');
                var h2 = helper.getNode('h2');

                h2.on('input', function (msg) {
                    msg.payload.should.equal('invalid_value');
                    msg.should.have.property('error');
                    msg.error.should.have.property('validValues');
                    done();
                });

                e.receive({ payload: 'invalid_value' });
            });
        });

        it('should handle __advance for binary engine', function (done) {
            var flow = [
                { id: 'c1', type: 'composeio-config', name: 'Config' },
                { id: 'e1', type: 'composeio-state-engine', name: 'Engine',
                  config: 'c1', namespace: 'ftl_arm', engineOverride: { type: 'binary' },
                  wires: [['h1'], ['h2']] },
                { id: 'h1', type: 'helper' },
                { id: 'h2', type: 'helper' }
            ];
            helper.load([configNode, engineNode], flow, function () {
                var e = helper.getNode('e1');
                var h1 = helper.getNode('h1');

                h1.on('input', function (msg) {
                    // Default is 'off', advance should go to 'on'
                    msg.payload.should.equal('on');
                    msg.composeio.stateEngine.should.have.property('previousValue', 'off');
                    msg.composeio.stateEngine.should.have.property('advancedTo', 'on');
                    done();
                });

                // Set up registry with default value
                var config = helper.getNode('c1');
                config.registry['ftl_arm'] = {
                    name: 'ftl_arm',
                    stateEngine: { type: 'binary', values: ['off', 'on'], default: 'off' },
                    lastValue: 'off'
                };

                e.receive({ payload: '__advance' });
            });
        });
    });
});
