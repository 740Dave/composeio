'use strict';

const should = require('should');
const stateEngines = require('../lib/state-engines');

describe('State Engines', function () {

    describe('resolveEngine', function () {
        it('should resolve binary shorthand', function () {
            const engine = stateEngines.resolveEngine('binary');
            engine.should.have.property('type', 'binary');
            engine.values.should.eql(['off', 'on']);
            engine.default.should.equal('off');
        });

        it('should resolve tri_level shorthand', function () {
            const engine = stateEngines.resolveEngine('tri_level');
            engine.should.have.property('type', 'tri_level');
            engine.values.should.eql(['off', 'half', 'full']);
        });

        it('should resolve stop_light shorthand', function () {
            const engine = stateEngines.resolveEngine('stop_light');
            engine.values.should.eql(['green', 'yellow', 'red']);
            engine.default.should.equal('green');
        });

        it('should resolve continuous_0_255 shorthand', function () {
            const engine = stateEngines.resolveEngine('continuous_0_255');
            engine.should.have.property('type', 'continuous_0_255');
            engine.default.should.equal(0);
        });

        it('should resolve json shorthand', function () {
            const engine = stateEngines.resolveEngine('json');
            engine.should.have.property('type', 'json');
        });

        it('should resolve enum object syntax', function () {
            const engine = stateEngines.resolveEngine({
                type: 'enum',
                values: ['standby', 'warp_1', 'warp_9'],
                default: 'standby'
            });
            engine.should.have.property('type', 'enum');
            engine.values.should.eql(['standby', 'warp_1', 'warp_9']);
            engine.default.should.equal('standby');
        });

        it('should return null for unknown shorthand', function () {
            const engine = stateEngines.resolveEngine('unknown_engine');
            should(engine).be.null();
        });
    });

    describe('validate', function () {
        it('should accept valid binary values', function () {
            const engine = stateEngines.resolveEngine('binary');
            stateEngines.validate('on', engine).valid.should.be.true();
            stateEngines.validate('off', engine).valid.should.be.true();
        });

        it('should reject invalid binary values', function () {
            const engine = stateEngines.resolveEngine('binary');
            const result = stateEngines.validate('maybe', engine);
            result.valid.should.be.false();
            result.validValues.should.eql(['off', 'on']);
        });

        it('should accept valid tri_level values', function () {
            const engine = stateEngines.resolveEngine('tri_level');
            stateEngines.validate('off', engine).valid.should.be.true();
            stateEngines.validate('half', engine).valid.should.be.true();
            stateEngines.validate('full', engine).valid.should.be.true();
        });

        it('should reject invalid tri_level values', function () {
            const engine = stateEngines.resolveEngine('tri_level');
            stateEngines.validate('medium', engine).valid.should.be.false();
        });

        it('should accept valid stop_light values', function () {
            const engine = stateEngines.resolveEngine('stop_light');
            stateEngines.validate('green', engine).valid.should.be.true();
            stateEngines.validate('yellow', engine).valid.should.be.true();
            stateEngines.validate('red', engine).valid.should.be.true();
        });

        it('should reject invalid stop_light values', function () {
            const engine = stateEngines.resolveEngine('stop_light');
            stateEngines.validate('blue', engine).valid.should.be.false();
        });

        it('should accept valid continuous_0_255 values', function () {
            const engine = stateEngines.resolveEngine('continuous_0_255');
            stateEngines.validate(0, engine).valid.should.be.true();
            stateEngines.validate(128, engine).valid.should.be.true();
            stateEngines.validate(255, engine).valid.should.be.true();
        });

        it('should reject out-of-range continuous_0_255 values', function () {
            const engine = stateEngines.resolveEngine('continuous_0_255');
            stateEngines.validate(-1, engine).valid.should.be.false();
            stateEngines.validate(256, engine).valid.should.be.false();
            stateEngines.validate(1.5, engine).valid.should.be.false();
            stateEngines.validate('abc', engine).valid.should.be.false();
        });

        it('should accept valid enum values', function () {
            const engine = stateEngines.resolveEngine({
                type: 'enum',
                values: ['standby', 'warp_1', 'warp_9'],
                default: 'standby'
            });
            stateEngines.validate('standby', engine).valid.should.be.true();
            stateEngines.validate('warp_1', engine).valid.should.be.true();
        });

        it('should reject invalid enum values', function () {
            const engine = stateEngines.resolveEngine({
                type: 'enum',
                values: ['standby', 'warp_1'],
                default: 'standby'
            });
            stateEngines.validate('warp_99', engine).valid.should.be.false();
        });

        it('should accept valid JSON values', function () {
            const engine = stateEngines.resolveEngine('json');
            stateEngines.validate({ key: 'value' }, engine).valid.should.be.true();
            stateEngines.validate([1, 2, 3], engine).valid.should.be.true();
            stateEngines.validate('hello', engine).valid.should.be.true();
            stateEngines.validate(42, engine).valid.should.be.true();
            stateEngines.validate(null, engine).valid.should.be.true();
        });

        it('should pass through when no engine is defined', function () {
            stateEngines.validate('anything', null).valid.should.be.true();
            stateEngines.validate('anything', {}).valid.should.be.true();
        });
    });

    describe('advance', function () {
        it('should advance binary from off to on', function () {
            const engine = stateEngines.resolveEngine('binary');
            stateEngines.advance('off', engine).should.equal('on');
        });

        it('should advance binary from on to off (wrap)', function () {
            const engine = stateEngines.resolveEngine('binary');
            stateEngines.advance('on', engine).should.equal('off');
        });

        it('should advance tri_level through cycle', function () {
            const engine = stateEngines.resolveEngine('tri_level');
            stateEngines.advance('off', engine).should.equal('half');
            stateEngines.advance('half', engine).should.equal('full');
            stateEngines.advance('full', engine).should.equal('off');
        });

        it('should advance stop_light through cycle', function () {
            const engine = stateEngines.resolveEngine('stop_light');
            stateEngines.advance('green', engine).should.equal('yellow');
            stateEngines.advance('yellow', engine).should.equal('red');
            stateEngines.advance('red', engine).should.equal('green');
        });

        it('should advance continuous_0_255 through toggle values', function () {
            const engine = stateEngines.resolveEngine('continuous_0_255');
            stateEngines.advance(0, engine).should.equal(128);
            stateEngines.advance(128, engine).should.equal(255);
            stateEngines.advance(255, engine).should.equal(0);
        });

        it('should advance enum through custom values', function () {
            const engine = stateEngines.resolveEngine({
                type: 'enum',
                values: ['a', 'b', 'c'],
                default: 'a'
            });
            stateEngines.advance('a', engine).should.equal('b');
            stateEngines.advance('b', engine).should.equal('c');
            stateEngines.advance('c', engine).should.equal('a');
        });

        it('should return current value for json engine', function () {
            const engine = stateEngines.resolveEngine('json');
            const obj = { key: 'val' };
            stateEngines.advance(obj, engine).should.equal(obj);
        });

        it('should return default when current value is unknown', function () {
            const engine = stateEngines.resolveEngine('binary');
            stateEngines.advance('unknown', engine).should.equal('off');
        });
    });
});
