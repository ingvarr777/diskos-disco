/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Trial detection and staging refusals in the production ota.c, against real files in a temp dir.
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
static char run_p[300], launched_p[300], upd_p[300], self_p[300], pub_p[300], ep_p[300], ver_p[300], src_p[300];
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

/* ota_stage_dir with a stub verifier: everything up to the commit is real */
static void make_bundle(const char *ui_bytes){
    char p[400];
    const char *names[] = { "keyauthz", "keyauthz.sig", "leafpub.pem", "update.manifest.sig" };
    for(int i = 0; i < 4; i++){ snprintf(p, sizeof p, "%s/%s", src_p, names[i]); put(p, "x\n"); }
    snprintf(p, sizeof p, "%s/mq_ui", src_p); put(p, ui_bytes);
    char sha[65]; assert(sha256_file(p, sha) == 0);
    char man[300]; snprintf(man, sizeof man, "diskos-update-v1\ncomponent=mq_ui\nepoch=500000\nversion=1.4.1\nsize=%zu\nsha256=%s\nchannel=sd\n", strlen(ui_bytes), sha);
    snprintf(p, sizeof p, "%s/update.manifest", src_p); put(p, man);
}
static void stage_refuses_rejected(void){
    ota_prog_t pg; memset(&pg, 0, sizeof pg);
    char rj[300], pend[300], sha[65], b[400];
    join(rj, "updates/rejected"); join(pend, "updates/pending/ready");
    make_bundle("ELF-ish test binary\n");
    snprintf(b, sizeof b, "%s/mq_ui", src_p); assert(sha256_file(b, sha) == 0);

    unlink(rj);
    assert(ota_stage_dir(&P, src_p, &pg) == OTA_OK);                       /* control: stages when nothing was rolled back */
    assert(access(pend, F_OK) == 0);
    assert(ota_discard_pending(&P) == 1);

    snprintf(b, sizeof b, "%064d\n%s\n%064d\n", 7, sha, 8);                /* S97 keeps the last 8 rolled-back hashes */
    put(rj, b);
    memset(&pg, 0, sizeof pg);
    assert(ota_stage_dir(&P, src_p, &pg) == OTA_E_REJECTED);
    assert(access(pend, F_OK) != 0);
    char lr[300]; join(lr, "updates/last_result");
    assert(slurp(lr, b, sizeof b) > 0 && strstr(b, "rolled back"));
    assert(ota_err_text(OTA_E_REJECTED) != NULL);

    snprintf(b, sizeof b, "%.63s\n", sha);                                  /* a prefix is not a match */
    put(rj, b);
    memset(&pg, 0, sizeof pg);
    assert(ota_stage_dir(&P, src_p, &pg) == OTA_OK);
    assert(ota_discard_pending(&P) == 1);
    puts("PASS SD staging refuses a build boot rolled back before, and only that build");
}

int main(void){
    assert(mkdtemp(root));
    join(run_p, "run"); join(launched_p, "launched"); join(upd_p, "updates"); join(self_p, "self_exe");
    join(pub_p, "root.pub.pem"); join(ep_p, "epochs"); join(ver_p, "verify.sh"); join(src_p, "sd");
    assert(mkdir(upd_p, 0700) == 0 && mkdir(src_p, 0700) == 0);
    put(self_p, "pretend this is the running mq_ui\n");
    assert(sha256_file(self_p, self_sha) == 0);
    put(pub_p, "pub\n");
    put(ep_p, "MIN_KEY_EPOCH=1\nMIN_UI_EPOCH=1\n");
    put(ver_p, "#!/bin/sh\necho \"OK component=mq_ui version=1.4.1 epoch=500000 keyepoch=1\"\n");
    P = OTA_DEV;
    P.updates = upd_p; P.rootpub = pub_p; P.epochs = ep_p; P.verifier = ver_p; P.data_root = root;
    P.run_file = run_p; P.launched_file = launched_p; P.self_exe = self_p;

    trial_record_sizes();
    not_a_trial();
    stage_refuses_rejected();
    puts("PASS ota_trial_test");
    return 0;
}
