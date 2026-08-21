/*
 * yaml_min.cpp — Minimal YAML-subset parser (see yaml_min.h)
 *
 * Pure C++, no ESP-IDF dependencies — host-compilable for desk tests.
 */

#include "yaml_min.h"

#include <cstdlib>
#include <cstring>
#include <cctype>

/* ------------------------------------------------------------------ */
/* Arena                                                                */
/* ------------------------------------------------------------------ */

void* YamlDoc::arena_alloc(size_t n) {
    /* 4-byte alignment */
    n = (n + 3u) & ~3u;
    if (_arena_used + n > _arena_cap) {
        _error = "yaml arena exhausted";
        return nullptr;
    }
    void* p = _arena + _arena_used;
    _arena_used += n;
    return p;
}

char* YamlDoc::arena_strndup(const char* s, size_t n) {
    char* p = static_cast<char*>(arena_alloc(n + 1));
    if (!p) return nullptr;
    memcpy(p, s, n);
    p[n] = '\0';
    return p;
}

YamlNode* YamlDoc::new_node(YamlNode::Type t) {
    YamlNode* n = static_cast<YamlNode*>(arena_alloc(sizeof(YamlNode)));
    if (!n) return nullptr;
    *n = YamlNode{};
    n->type = t;
    return n;
}

YamlDoc::~YamlDoc() {
    free(_arena);
    free(_lines);
}

/* ------------------------------------------------------------------ */
/* Small helpers                                                        */
/* ------------------------------------------------------------------ */

static void append_child(YamlNode* parent, YamlNode* child) {
    if (!parent->first_child) {
        parent->first_child = child;
    } else {
        YamlNode* c = parent->first_child;
        while (c->next_sibling) c = c->next_sibling;
        c->next_sibling = child;
    }
    parent->count++;
}

/* Find the ':' that separates key from value at top nesting level.
 * A key-separating colon must be followed by space/end ("a: b", "a:"). */
static const char* find_key_colon(const char* s) {
    int depth = 0;
    char quote = 0;
    for (const char* p = s; *p; p++) {
        char c = *p;
        if (quote) {
            if (c == quote) quote = 0;
            continue;
        }
        if (c == '\'' || c == '"') { quote = c; continue; }
        if (c == '[' || c == '{') depth++;
        else if (c == ']' || c == '}') depth--;
        else if (c == ':' && depth == 0 && (p[1] == ' ' || p[1] == '\0')) {
            return p;
        }
    }
    return nullptr;
}

static const char* skip_ws(const char* s) {
    while (*s == ' ' || *s == '\t') s++;
    return s;
}

static size_t rtrim_len(const char* s, size_t n) {
    while (n > 0 && (s[n - 1] == ' ' || s[n - 1] == '\t')) n--;
    return n;
}

/* ------------------------------------------------------------------ */
/* Line splitting (comment stripping is quote-aware)                    */
/* ------------------------------------------------------------------ */

const YamlNode* YamlDoc::parse(const char* text, size_t len) {
    _error = "";

    /* Arena sizing: input copy + generous node overhead.  Configs are
     * a few KB; this stays well inside the boot heap budget. */
    _arena_cap = len * 2 + 8192 + (len / 8) * sizeof(YamlNode);
    _arena = static_cast<char*>(malloc(_arena_cap));
    if (!_arena) { _error = "yaml arena malloc failed"; return nullptr; }
    _arena_used = 0;

    /* Working copy we can mutate (NUL-terminate lines in place). */
    char* buf = arena_strndup(text, len);
    if (!buf) return nullptr;

    /* Pre-pass: quote-aware comment stripping + multi-line flow joining.
     * Comments are blanked to spaces; a newline inside an open flow
     * collection ([…] / {…} spanning lines, as Composer/agents emit)
     * becomes a space so the line splitter sees one logical line. */
    {
        int depth = 0;
        char quote = 0;
        char prev = '\n';
        for (size_t i = 0; i < len; i++) {
            char c = buf[i];
            if (c == '\n') {
                quote = 0;                      /* quotes don't span lines */
                if (depth > 0) buf[i] = ' ';
                prev = '\n';
                continue;
            }
            if (quote) {
                if (c == quote) quote = 0;
                prev = c;
                continue;
            }
            if (c == '\'' || c == '"') {
                quote = c;
            } else if (c == '#' && (prev == ' ' || prev == '\t' || prev == '\n')) {
                while (i < len && buf[i] != '\n') buf[i++] = ' ';
                if (i < len) { /* newline: reprocess via loop */ i--; }
                prev = ' ';
                continue;
            } else if (c == '[' || c == '{') {
                depth++;
            } else if (c == ']' || c == '}') {
                if (depth > 0) depth--;
            }
            prev = c;
        }
    }

    /* Count lines for the index. */
    int max_lines = 1;
    for (size_t i = 0; i < len; i++) if (buf[i] == '\n') max_lines++;
    _lines = static_cast<Line*>(malloc(sizeof(Line) * max_lines));
    if (!_lines) { _error = "yaml line index malloc failed"; return nullptr; }
    _line_count = 0;

    char* p = buf;
    while (*p) {
        char* line = p;
        char* nl = strchr(p, '\n');
        if (nl) { *nl = '\0'; p = nl + 1; } else { p += strlen(p); }

        /* Strip trailing CR */
        size_t llen = strlen(line);
        if (llen && line[llen - 1] == '\r') { line[--llen] = '\0'; }

        /* Indent */
        int indent = 0;
        while (line[indent] == ' ') indent++;
        if (line[indent] == '\t') { _error = "tabs in indentation"; return nullptr; }
        char* content = line + indent;

        /* Quote-aware comment strip: '#' at start or preceded by space */
        char quote = 0;
        for (char* q = content; *q; q++) {
            if (quote) { if (*q == quote) quote = 0; continue; }
            if (*q == '\'' || *q == '"') { quote = *q; continue; }
            if (*q == '#' && (q == content || q[-1] == ' ' || q[-1] == '\t')) {
                *q = '\0';
                break;
            }
        }

        /* Right-trim */
        size_t clen = rtrim_len(content, strlen(content));
        content[clen] = '\0';
        if (clen == 0) continue;               /* blank / comment-only */

        _lines[_line_count].text = content;
        _lines[_line_count].indent = indent;
        _line_count++;
    }

    if (_line_count == 0) { _error = "empty document"; return nullptr; }

    int idx = 0;
    _root = parse_block(idx, 0, false);
    if (_root && idx < _line_count) {
        /* Trailing content the block walk could not attach — indentation
         * error in the document. Fail loudly rather than render half. */
        _error = "unattached trailing lines (indentation error?)";
        return nullptr;
    }
    return _root;
}

/* ------------------------------------------------------------------ */
/* Block parser                                                         */
/* ------------------------------------------------------------------ */

YamlNode* YamlDoc::parse_block(int& idx, int min_indent, bool /*unused*/) {
    if (idx >= _line_count) return nullptr;

    const int block_indent = _lines[idx].indent;
    if (block_indent < min_indent) return nullptr;

    const bool is_list = (_lines[idx].text[0] == '-' &&
                          (_lines[idx].text[1] == ' ' || _lines[idx].text[1] == '\0'));

    YamlNode* node = new_node(is_list ? YamlNode::Type::List : YamlNode::Type::Map);
    if (!node) return nullptr;

    while (idx < _line_count && _lines[idx].indent == block_indent) {
        const char* content = _lines[idx].text;

        if (is_list) {
            if (!(content[0] == '-' && (content[1] == ' ' || content[1] == '\0'))) {
                break;  /* end of the sequence */
            }
            const char* rest = (content[1] == '\0') ? content + 1
                                                    : skip_ws(content + 2);
            if (*rest == '\0') {
                /* "-" alone: item is the following deeper block */
                idx++;
                YamlNode* child = nullptr;
                if (idx < _line_count && _lines[idx].indent > block_indent) {
                    child = parse_block(idx, block_indent + 1, false);
                } else {
                    child = new_node(YamlNode::Type::Scalar);
                    if (child) child->scalar = "";
                }
                if (!child) return nullptr;
                append_child(node, child);
            } else if (find_key_colon(rest)) {
                /* "- key: ..." — inline map start.  Rewrite the line to the
                 * map's virtual indent and re-enter the map parser. */
                int virt = block_indent + (int)(rest - content);
                _lines[idx].text = rest;
                _lines[idx].indent = virt;
                YamlNode* child = parse_block(idx, virt, false);
                if (!child) return nullptr;
                append_child(node, child);
            } else {
                /* plain scalar / flow item */
                YamlNode* child = parse_value(rest);
                if (!child) return nullptr;
                append_child(node, child);
                idx++;
            }
        } else {
            /* Map entry */
            const char* colon = find_key_colon(content);
            if (!colon) { _error = "expected 'key:' line"; return nullptr; }

            size_t klen = rtrim_len(content, (size_t)(colon - content));
            /* strip quotes on key */
            const char* kstart = content;
            if (klen >= 2 && (kstart[0] == '"' || kstart[0] == '\'') &&
                kstart[klen - 1] == kstart[0]) {
                kstart++; klen -= 2;
            }
            char* key = arena_strndup(kstart, klen);
            if (!key) return nullptr;

            const char* vstr = skip_ws(colon + 1);
            YamlNode* child = nullptr;

            if (*vstr != '\0') {
                child = parse_value(vstr);
                idx++;
            } else {
                idx++;
                bool same_indent_list =
                    idx < _line_count &&
                    _lines[idx].indent == block_indent &&
                    _lines[idx].text[0] == '-' &&
                    (_lines[idx].text[1] == ' ' || _lines[idx].text[1] == '\0');
                if (idx < _line_count &&
                    (_lines[idx].indent > block_indent || same_indent_list)) {
                    /* Nested block; YAML also allows a sequence at the SAME
                     * indent as its parent key. */
                    child = parse_block(idx, _lines[idx].indent, false);
                } else {
                    child = new_node(YamlNode::Type::Scalar);   /* empty value */
                    if (child) child->scalar = "";
                }
            }
            if (!child) return nullptr;
            child->key = key;
            append_child(node, child);
        }
    }

    return node;
}

/* ------------------------------------------------------------------ */
/* Value / flow parsing                                                 */
/* ------------------------------------------------------------------ */

YamlNode* YamlDoc::parse_value(const char* s) {
    s = skip_ws(s);
    if (*s == '[' || *s == '{') {
        const char* p = s;
        return parse_flow(p);
    }
    YamlNode* n = new_node(YamlNode::Type::Scalar);
    if (!n) return nullptr;

    size_t len = rtrim_len(s, strlen(s));
    if (len >= 2 && (s[0] == '"' || s[0] == '\'') && s[len - 1] == s[0]) {
        n->scalar = arena_strndup(s + 1, len - 2);
    } else {
        n->scalar = arena_strndup(s, len);
    }
    return n->scalar ? n : nullptr;
}

/* Reads one flow scalar token, stopping at , ] } : at depth 0. */
const char* YamlDoc::parse_flow_scalar(const char*& s) {
    s = skip_ws(s);
    const char* start = s;
    char quote = 0;
    if (*s == '\'' || *s == '"') {
        quote = *s;
        start = ++s;
        while (*s && *s != quote) s++;
        const char* out = arena_strndup(start, (size_t)(s - start));
        if (*s == quote) s++;
        return out;
    }
    while (*s && *s != ',' && *s != ']' && *s != '}' &&
           !(*s == ':' && (s[1] == ' ' || s[1] == ',' || s[1] == '}' ))) {
        s++;
    }
    size_t len = rtrim_len(start, (size_t)(s - start));
    return arena_strndup(start, len);
}

YamlNode* YamlDoc::parse_flow(const char*& s) {
    s = skip_ws(s);
    if (*s == '[') {
        s++;
        YamlNode* list = new_node(YamlNode::Type::List);
        if (!list) return nullptr;
        s = skip_ws(s);
        while (*s && *s != ']') {
            YamlNode* item = nullptr;
            if (*s == '[' || *s == '{') {
                item = parse_flow(s);
            } else {
                item = new_node(YamlNode::Type::Scalar);
                if (item) item->scalar = parse_flow_scalar(s);
            }
            if (!item || (item->type == YamlNode::Type::Scalar && !item->scalar)) {
                return nullptr;
            }
            append_child(list, item);
            s = skip_ws(s);
            if (*s == ',') { s++; s = skip_ws(s); }
        }
        if (*s == ']') s++;
        return list;
    }
    if (*s == '{') {
        s++;
        YamlNode* map = new_node(YamlNode::Type::Map);
        if (!map) return nullptr;
        s = skip_ws(s);
        while (*s && *s != '}') {
            const char* key = parse_flow_scalar(s);
            if (!key) return nullptr;
            s = skip_ws(s);
            if (*s == ':') s++;
            s = skip_ws(s);
            YamlNode* val = nullptr;
            if (*s == '[' || *s == '{') {
                val = parse_flow(s);
            } else {
                val = new_node(YamlNode::Type::Scalar);
                if (val) val->scalar = parse_flow_scalar(s);
            }
            if (!val || (val->type == YamlNode::Type::Scalar && !val->scalar)) {
                return nullptr;
            }
            val->key = key;
            append_child(map, val);
            s = skip_ws(s);
            if (*s == ',') { s++; s = skip_ws(s); }
        }
        if (*s == '}') s++;
        return map;
    }
    _error = "expected flow collection";
    return nullptr;
}

/* ------------------------------------------------------------------ */
/* Accessors                                                            */
/* ------------------------------------------------------------------ */

const YamlNode* YamlNode::get(const char* k) const {
    if (type != Type::Map) return nullptr;
    for (const YamlNode* c = first_child; c; c = c->next_sibling) {
        if (c->key && strcmp(c->key, k) == 0) return c;
    }
    return nullptr;
}

const char* YamlNode::get_str(const char* k, const char* dflt) const {
    const YamlNode* c = get(k);
    return (c && c->type == Type::Scalar && c->scalar) ? c->scalar : dflt;
}

bool YamlNode::as_float(float& out) const {
    if (type != Type::Scalar || !scalar || !*scalar) return false;
    char* end = nullptr;
    /* strtol first so 0x… hex parses; fall through to float */
    long lv = strtol(scalar, &end, 0);
    if (end && *end == '\0') { out = (float)lv; return true; }
    float fv = strtof(scalar, &end);
    if (end && *end == '\0') { out = fv; return true; }
    return false;
}

bool YamlNode::as_int(int& out) const {
    float f;
    if (!as_float(f)) return false;
    out = (int)(f >= 0 ? f + 0.5f : f - 0.5f);
    return true;
}

bool YamlNode::as_bool(bool& out) const {
    if (type != Type::Scalar || !scalar) return false;
    /* YAML-1.1 canonicalization, matching the engine's trigger/when
     * handling ([CXC-BIND-BEHAVIOR]): on/true/yes/1 ≡ true. */
    if (!strcmp(scalar, "true") || !strcmp(scalar, "yes") ||
        !strcmp(scalar, "on") || !strcmp(scalar, "1")) { out = true; return true; }
    if (!strcmp(scalar, "false") || !strcmp(scalar, "no") ||
        !strcmp(scalar, "off") || !strcmp(scalar, "0")) { out = false; return true; }
    return false;
}

float YamlNode::get_float(const char* k, float dflt) const {
    const YamlNode* c = get(k);
    float v;
    return (c && c->as_float(v)) ? v : dflt;
}

int YamlNode::get_int(const char* k, int dflt) const {
    const YamlNode* c = get(k);
    int v;
    return (c && c->as_int(v)) ? v : dflt;
}

bool YamlNode::get_bool(const char* k, bool dflt) const {
    const YamlNode* c = get(k);
    bool v;
    return (c && c->as_bool(v)) ? v : dflt;
}
