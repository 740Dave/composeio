/*
 * yaml_min.h — Minimal YAML-subset parser for .compose.yaml configs
 *
 * Spec: [CXC-R-BOOT] step 6 ("Parse widget tree from config") — the
 * embedded tier needs to parse the SAME .compose.yaml the desktop
 * engine parses, on a heap-constrained ESP32.  This is NOT a general
 * YAML parser; it is the bounded subset the .compose.yaml dialects
 * actually use:
 *
 *   - block mappings          key: value  /  key:\n  <indented block>
 *   - block sequences         - item  /  - key: value  (inline map start)
 *   - flow sequences          [1, 2, 3]          (single line)
 *   - flow mappings           {a: 1, b: two}     (single line, nestable)
 *   - scalars                 int, hex (0x..), float, bool, strings
 *                             (bare / 'single' / "double" quoted)
 *   - comments                # to end of line (quote-aware)
 *
 * NOT supported (documented degradation — parse error or ignored):
 *   anchors/aliases, multi-document, block scalars (| >), tags,
 *   multi-line flow collections, complex keys.
 *
 * Memory model: one bump arena sized from the input; all nodes and
 * strings live in it.  The returned tree (and every const char* in it)
 * is valid until YamlDoc is destroyed — the scene keeps the doc alive
 * for the lifetime of the composition and frees it on config reload.
 *
 * Pure C++ (no ESP-IDF includes) so it host-compiles for desk testing:
 *   see cyd/tools/yaml_min_test.cpp.
 */

#ifndef YAML_MIN_H
#define YAML_MIN_H

#include <cstddef>
#include <cstdint>

struct YamlNode {
    enum class Type : uint8_t { Scalar, Map, List };

    Type type = Type::Scalar;
    const char* key = nullptr;      /* set when this node is a map entry */
    const char* scalar = nullptr;   /* set when type == Scalar */
    YamlNode* first_child = nullptr;
    YamlNode* next_sibling = nullptr;
    int count = 0;                  /* number of children (Map/List) */

    /* -- typed accessors (Map nodes) -------------------------------- */
    const YamlNode* get(const char* k) const;                    /* child by key */
    const char* get_str(const char* k, const char* dflt) const;
    float get_float(const char* k, float dflt) const;
    int get_int(const char* k, int dflt) const;
    bool get_bool(const char* k, bool dflt) const;

    /* -- scalar coercion -------------------------------------------- */
    bool as_float(float& out) const;
    bool as_int(int& out) const;
    bool as_bool(bool& out) const;
};

class YamlDoc {
public:
    YamlDoc() = default;
    ~YamlDoc();
    YamlDoc(const YamlDoc&) = delete;
    YamlDoc& operator=(const YamlDoc&) = delete;

    /**
     * Parse a config. Copies the text into an internal arena; the input
     * buffer may be freed after parse() returns.
     * Returns nullptr on failure (see error() for a message).
     */
    const YamlNode* parse(const char* text, size_t len);

    const YamlNode* root() const { return _root; }
    const char* error() const { return _error; }
    size_t arena_used() const { return _arena_used; }

private:
    YamlNode* _root = nullptr;
    char* _arena = nullptr;
    size_t _arena_cap = 0;
    size_t _arena_used = 0;
    const char* _error = "";

    void* arena_alloc(size_t n);
    char* arena_strndup(const char* s, size_t n);
    YamlNode* new_node(YamlNode::Type t);

    /* parsing internals */
    struct Line { const char* text; int indent; };
    Line* _lines = nullptr;
    int _line_count = 0;

    YamlNode* parse_block(int& idx, int min_indent, bool as_list_item_body);
    YamlNode* parse_value(const char* s);
    YamlNode* parse_flow(const char*& s);
    const char* parse_flow_scalar(const char*& s);

    friend struct YamlNode;
};

#endif /* YAML_MIN_H */
