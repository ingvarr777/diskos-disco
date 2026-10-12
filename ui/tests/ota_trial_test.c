/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Trial detection in the production ota.c, against real run/launched records in a temp dir.
 * Run: make -C ui/tests ota_trial_test && ui/tests/ota_trial_test */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "../ota.c"

/* ota.c's only project dependencies; sys_kv is the same KEY=VALUE reader as system.c's */
int sys_kv(const char *text, const char *key, char *out, size_t n){
    size_t kl = strlen(key);
    for(const char *l = text; l && *l; ){
        const char *nl = strchr(l, '\n');
        size_t len = nl ? (size_t)(nl - l) : strlen(l);
        if(len > kl && !strncmp(l, key, kl) && l[kl] == '='){
            size_t vl = len - kl - 1;
            while(vl && (l[kl+1+vl-1] == '\r' || l[kl+1+vl-1] == ' ')) vl--;
            if(vl >= n) vl = n - 1;
            memcpy(out, l + kl + 1, vl); out[vl] = 0;
            return 0;
        }
        l = nl ? nl + 1 : NULL;
    }
    return -1;
}
int sys_parse_tag(const char *json, char *tag, size_t n){ (void)json; (void)tag; (void)n; return -1; }
int command_spawn_io(pid_t *pid, char *const argv[], int in_fd, int fd3){
    (void)pid; (void)argv; (void)in_fd; (void)fd3; return -1;          /* downloads are not exercised here */
}

static char root[] = "/tmp/diskos-ota-trial-XXXXXX";
static char run_p[300], launched_p[300], upd_p[300], self_p[300];
static ota_paths_t P;
static char self_sha[65];

static void put(const char *path, const char *s){
    FILE *f = fopen(path, "w"); assert(f); assert(fputs(s, f) >= 0); assert(!fclose(f));
}
static int slurp(const char *path, char *b, size_t n){
    FILE *f = fopen(path, "r"); if(!f) return -1;
    size_t r = fread(b, 1, n - 1, f); fclose(f); b[r] = 0; return (int)r;
}
static void join(char *out, const char *name){ snprintf(out, 300, "%s/%s", root, name); }

/* S97's write_run, byte for byte: the running build, then GOOD_* and PREV_* when they exist */
static size_t write_run(const char *build, const char *sha, int good, int prev){
    char b[2048]; int n = snprintf(b, sizeof b, "SHA256=%s\nSIZE=3456789\nBUILD=%s\nEPOCH=408024\nVERSION=1.3.1-Disco\n", sha, build);
    if(good) n += snprintf(b + n, sizeof b - n, "GOOD_SHA256=%064d\nGOOD_SIZE=3456000\nGOOD_EPOCH=406532\nGOOD_VERSION=1.3.0-Disco\n", 1);
    if(prev) n += snprintf(b + n, sizeof b - n, "PREV_SHA256=%064d\nPREV_SIZE=3455000\nPREV_EPOCH=405000\nPREV_VERSION=1.2.9-Disco-longer-label-x\n", 2);
    put(run_p, b);
    return (size_t)n;
}
static void write_launched(const char *build, const char *sha){
    char b[300]; snprintf(b, sizeof b, "SHA256=%s\nBUILD=%s\nEPOCH=408024\nVERSION=1.3.1-Disco\n", sha, build);
    put(launched_p, b);
}

static void trial_record_sizes(void){
    /* 0: first update ever (no good build); 1: a good build is kept (the case from the bug report); 2: good + good.prev */
    const int shapes[3][2] = { {0, 0}, {1, 0}, {1, 1} };
    for(int i = 0; i < 3; i++){
        size_t sz = write_run("trial", self_sha, shapes[i][0], shapes[i][1]);
        write_launched("trial", self_sha);
        char ver[40] = "";
        int r = ota_trial_version(&P, ver, sizeof ver);
        printf("  run record %zu bytes: trial=%d\n", sz, r);
        assert(r == 1);
        assert(!strcmp(ver, "1.3.1-Disco"));
        char hp[300], b[200]; join(hp, "updates/healthy"); unlink(hp);
        assert(ota_health_write(&P) == 1);                                  /* the auto proof and "Keep" both go through here */
        assert(slurp(hp, b, sizeof b) > 0);
        char want[80]; snprintf(want, sizeof want, "SHA256=%s\n", self_sha);
        assert(!strcmp(b, want));
        char rp[300]; join(rp, "updates/revert"); unlink(rp);
        assert(ota_revert_write(&P) == 1);                                  /* "Go back" */
        unlink(rp);
    }
    assert(write_run("trial", self_sha, 1, 1) > 255);                       /* the shape that used to be refused */
    puts("PASS a trial is recognised, and can write healthy/revert, with GOOD_* and PREV_* in the run record");
}

static void not_a_trial(void){
    write_run("good", self_sha, 0, 0); write_launched("good", self_sha);
    assert(ota_trial_version(&P, NULL, 0) == 0);
    write_run("trial", self_sha, 1, 0); write_launched("good", self_sha);  /* the launcher fell back to the good build */
    assert(ota_trial_version(&P, NULL, 0) == 0);
    assert(ota_health_write(&P) == 0);
    char other[65]; memset(other, 'a', 64); other[64] = 0;
    write_run("trial", other, 1, 1); write_launched("trial", other);       /* agreeing records, but not this executable */
    assert(ota_trial_version(&P, NULL, 0) == 1);
    assert(ota_health_write(&P) == 0);
    char big[RUN_REC_MAX + 16]; memset(big, '#', sizeof big - 1); big[sizeof big - 1] = 0;
    put(run_p, big); write_launched("trial", self_sha);                    /* oversize: still refused, never truncated */
    assert(ota_trial_version(&P, NULL, 0) == 0);
    puts("PASS fallback, foreign and oversize run records are not treated as this trial");
}

int main(void){
    assert(mkdtemp(root));
    join(run_p, "run"); join(launched_p, "launched"); join(upd_p, "updates"); join(self_p, "self_exe");
    assert(mkdir(upd_p, 0700) == 0);
    put(self_p, "pretend this is the running mq_ui\n");
    assert(sha256_file(self_p, self_sha) == 0);
    P = OTA_DEV;
    P.updates = upd_p;
    P.run_file = run_p; P.launched_file = launched_p; P.self_exe = self_p;

    trial_record_sizes();
    not_a_trial();
    puts("PASS ota_trial_test");
    return 0;
}
