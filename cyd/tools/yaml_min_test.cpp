/*
 * yaml_min_test.cpp — host desk test for the minimal YAML parser.
 *
 * Build + run:
 *   clang++ -std=c++17 -I../src -o /tmp/yaml_min_test yaml_min_test.cpp ../src/yaml_min.cpp
 *   /tmp/yaml_min_test <file.compose.yaml> [...]
 *
 * Dumps the parsed tree; exits non-zero on parse failure.
 */

#include "../src/yaml_min.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

static void dump(const YamlNode* n, int depth) {
    for (int i = 0; i < depth; i++) printf("  ");
    if (n->key) printf("%s: ", n->key);
    switch (n->type) {
        case YamlNode::Type::Scalar:
            printf("'%s'\n", n->scalar ? n->scalar : "(null)");
            break;
        case YamlNode::Type::Map:
            printf("{map, %d}\n", n->count);
            for (const YamlNode* c = n->first_child; c; c = c->next_sibling)
                dump(c, depth + 1);
            break;
        case YamlNode::Type::List:
            printf("[list, %d]\n", n->count);
            for (const YamlNode* c = n->first_child; c; c = c->next_sibling)
                dump(c, depth + 1);
            break;
    }
}

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s file...\n", argv[0]); return 2; }
    int rc = 0;
    for (int i = 1; i < argc; i++) {
        FILE* f = fopen(argv[i], "rb");
        if (!f) { fprintf(stderr, "cannot open %s\n", argv[i]); rc = 1; continue; }
        fseek(f, 0, SEEK_END);
        long len = ftell(f);
        fseek(f, 0, SEEK_SET);
        char* buf = (char*)malloc(len + 1);
        fread(buf, 1, len, f);
        buf[len] = '\0';
        fclose(f);

        YamlDoc doc;
        const YamlNode* root = doc.parse(buf, (size_t)len);
        printf("==== %s ====\n", argv[i]);
        if (!root) {
            printf("PARSE FAILED: %s\n", doc.error());
            rc = 1;
        } else {
            printf("(arena used: %zu bytes)\n", doc.arena_used());
            dump(root, 0);
        }
        free(buf);
    }
    return rc;
}
