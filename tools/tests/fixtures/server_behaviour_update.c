#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "behavior_patch.h"

#define MS __attribute__((ms_abi))
#define GEAR_TABLE UINT64_C(0x143a5bf40)
#define BEHAVIOR_TABLE UINT64_C(0x143b00000)
#define ORIGINAL UINT64_C(0x14879d8c0)
#define RESULT UINT64_C(0x1abcdeffedcba001)

struct behavior {
    uintptr_t table;
    uintptr_t reserved;
    void *owner;
    unsigned char ready;
    unsigned checks, updates;
};
struct update_info {
    float delta;
    uint32_t padding;
    void *gear_state, *context;
    uint64_t zero1, zero2;
};
static unsigned original_calls;
static unsigned char *expected_gear, *expected_context;

static uint64_t MS original_update(void *instance, void *context) {
    ++original_calls;
    assert(instance == expected_gear + 0x3e0);
    assert(context == expected_context);
    return RESULT;
}
static unsigned char MS ready(struct behavior *behavior) {
    ++behavior->checks;
    return behavior->ready;
}
static void MS update(struct behavior *behavior, const struct update_info *info) {
    ++behavior->updates;
    assert(info->delta == 0.125f);
    assert(info->gear_state == expected_gear + 0x2f8);
    assert(info->context == expected_context);
    assert(info->zero1 == 0 && info->zero2 == 0);
}
static void qword(void *address, uintptr_t value) { memcpy(address, &value, 8); }

int main(void) {
    void *table = mmap((void *)(uintptr_t)BEHAVIOR_TABLE, 4096,
                      PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (table == MAP_FAILED) {
        fprintf(stderr, "Cannot reserve fixture vtable: %s\n", strerror(errno));
        return errno == EEXIST || errno == EINVAL ? 77 : 1;
    }
    qword((char *)table + 0x20, (uintptr_t)ready);
    qword((char *)table + 0x30, (uintptr_t)update);
    assert(mprotect(table, 4096, PROT_READ) == 0);
    void *code = mmap(NULL, sizeof(patch), PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(code != MAP_FAILED);
    memcpy(code, patch, sizeof(patch));
    unsigned replaced = 0;
    for (size_t i = 0; i + 8 <= sizeof(patch); ++i) {
        uint64_t address;
        memcpy(&address, (char *)code + i, 8);
        if (address == ORIGINAL) {
            qword((char *)code + i, (uintptr_t)original_update);
            ++replaced;
        }
    }
    assert(replaced == 1);
    assert(mprotect(code, sizeof(patch), PROT_READ | PROT_EXEC) == 0);
    uint64_t (MS *run)(void *, void *) = code;
    expected_gear = calloc(1, 0x500);
    expected_context = calloc(1, 0x600);
    struct behavior *behaviors = calloc(4, sizeof(*behaviors));
    assert(expected_gear && expected_context && behaviors);
    const float delta = 0.125f;
    memcpy(expected_context + 0x528, &delta, sizeof(delta));

    for (unsigned scenario = 0; scenario < 8; ++scenario) {
        memset(behaviors, 0, 4 * sizeof(*behaviors));
        qword(expected_gear, GEAR_TABLE);
        original_calls = 0;
        unsigned checks[4] = {1, 1, 1, 1}, updates[4] = {1, 1, 1, 1};
        for (unsigned i = 0; i < 4; ++i) {
            behaviors[i].table = BEHAVIOR_TABLE;
            behaviors[i].owner = expected_gear + 0x280;
            behaviors[i].ready = 1;
            qword(expected_gear + 0x2b8 + i * 8, (uintptr_t)&behaviors[i]);
        }
        switch (scenario) {
        case 0: break;
        case 1: qword(expected_gear + 0x2b8, 0); checks[0] = updates[0] = 0; break;
        case 2: behaviors[0].owner = expected_gear + 0x460; checks[0] = updates[0] = 0; break;
        case 3:
            qword(expected_gear, GEAR_TABLE + 8);
            memset(checks, 0, sizeof(checks)); memset(updates, 0, sizeof(updates)); break;
        case 4: behaviors[2].ready = 0; updates[2] = 0; break;
        case 5:
            memset(expected_gear + 0x2b8, 0, 32);
            memset(checks, 0, sizeof(checks)); memset(updates, 0, sizeof(updates)); break;
        case 6:
            qword(expected_gear + 0x2b8, (uintptr_t)&behaviors[0] + 1);
            checks[0] = updates[0] = 0; break;
        case 7: behaviors[0].table = 0x10000; checks[0] = updates[0] = 0; break;
        }
        assert(run(expected_gear + 0x3e0, expected_context) == RESULT);
        assert(original_calls == 1);
        for (unsigned i = 0; i < 4; ++i) {
            assert(behaviors[i].checks == checks[i]);
            assert(behaviors[i].updates == updates[i]);
        }
    }
    puts("8 native behavior-update cases passed");
    free(behaviors); free(expected_context); free(expected_gear);
    munmap(code, sizeof(patch)); munmap(table, 4096);
    return 0;
}
