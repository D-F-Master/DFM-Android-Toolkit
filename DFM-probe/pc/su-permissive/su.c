/* Minimal permissive su for factory test devices (replaces toybox su).
 * Compatible with toybox usage: su [WHO [COMMAND...]]
 * - optional leading "-" flags are ignored
 * - WHO (uid/name) parsed but we always switch to root (test devices only)
 * - remaining args exec'd directly (covers: su 0 sh -c <script>, su 0 id, su)
 */
#include <unistd.h>
#include <ctype.h>
#include <string.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    int i = 1;
    while (i < argc && argv[i][0] == '-') i++;          /* skip -l - etc */
    if (i < argc && argv[i][0] != '/' && strchr(argv[i], '/') == NULL &&
        strlen(argv[i]) > 0 && (isdigit((unsigned char)argv[i][0]) || isalpha((unsigned char)argv[i][0]))) {
        /* looks like a WHO argument (digits or username): skip it, always become root */
        i++;
    }
    (void)argc;
    setgroups(0, NULL);
    setgid(0);
    setuid(0);
    setenv("PATH", "/product/bin:/apex/com.android.runtime/bin:/apex/com.android.art/bin:/system_ext/bin:/system/bin:/system/xbin:/odm/bin:/vendor/bin:/vendor/xbin", 1);
    setenv("USER", "root", 1);
    setenv("LOGNAME", "root", 1);
    if (i >= argc) {
        execl("/system/bin/sh", "sh", (char *)NULL);
        return 127;
    }
    execvp(argv[i], &argv[i]);
    return 127;
}
