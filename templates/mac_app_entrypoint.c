#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int parent_dir(char *path) {
  char *slash = strrchr(path, '/');
  if (slash == NULL || slash == path) {
    return -1;
  }
  *slash = '\0';
  return 0;
}

static int join_path(char *out, size_t out_size, const char *left, const char *right) {
  int written = snprintf(out, out_size, "%s/%s", left, right);
  return written > 0 && (size_t)written < out_size ? 0 : -1;
}

static int mkdir_p(const char *path) {
  char buffer[PATH_MAX];
  size_t length = strlen(path);
  if (length == 0 || length >= sizeof(buffer)) {
    return -1;
  }
  memcpy(buffer, path, length + 1);
  for (char *p = buffer + 1; *p != '\0'; p++) {
    if (*p == '/') {
      *p = '\0';
      if (mkdir(buffer, 0755) != 0 && errno != EEXIST) {
        return -1;
      }
      *p = '/';
    }
  }
  if (mkdir(buffer, 0755) != 0 && errno != EEXIST) {
    return -1;
  }
  return 0;
}

static void write_python_missing_state(void) {
  const char *home = getenv("HOME");
  if (home == NULL || home[0] == '\0') {
    return;
  }

  char support[PATH_MAX];
  char logs[PATH_MAX];
  char state[PATH_MAX];
  char log_path[PATH_MAX];
  if (snprintf(support, sizeof(support), "%s/Library/Application Support/TotalSegmentatorWrapperMac", home) >= (int)sizeof(support)) {
    return;
  }
  if (join_path(logs, sizeof(logs), support, "logs") != 0 ||
      join_path(state, sizeof(state), support, "setup_state.json") != 0 ||
      join_path(log_path, sizeof(log_path), logs, "launcher.log") != 0) {
    return;
  }

  mkdir_p(logs);

  FILE *state_file = fopen(state, "w");
  if (state_file != NULL) {
    fputs("{\n", state_file);
    fputs("  \"schema\": \"totalsegmentator_wrapper_mac.setup_state.v1\",\n", state_file);
    fputs("  \"status\": \"failed\",\n", state_file);
    fputs("  \"reason\": \"python312_missing\",\n", state_file);
    fputs("  \"error\": \"Bundled Python 3.12 is missing and TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312 was not supplied.\"\n", state_file);
    fputs("}\n", state_file);
    fclose(state_file);
  }

  FILE *log_file = fopen(log_path, "a");
  if (log_file != NULL) {
    fputs("TotalSegmentator Wrapper for Mac requires bundled Python 3.12 or TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312.\n", log_file);
    fclose(log_file);
  }
}

static void set_app_support_python_cache(void) {
  const char *home = getenv("HOME");
  if (home == NULL || home[0] == '\0') {
    return;
  }

  char pycache[PATH_MAX];
  if (snprintf(
          pycache,
          sizeof(pycache),
          "%s/Library/Application Support/TotalSegmentatorWrapperMac/cache/pycache",
          home) >= (int)sizeof(pycache)) {
    return;
  }
  setenv("PYTHONPYCACHEPREFIX", pycache, 1);
}

int main(int argc, char **argv) {
  char executable[PATH_MAX];
  uint32_t executable_size = sizeof(executable);
  if (_NSGetExecutablePath(executable, &executable_size) != 0) {
    fputs("TotalSegmentatorWrapperForMac launcher path is too long.\n", stderr);
    return 127;
  }

  char resolved[PATH_MAX];
  if (realpath(executable, resolved) == NULL) {
    perror("realpath");
    return 127;
  }

  char macos_dir[PATH_MAX];
  char contents_dir[PATH_MAX];
  char resources_dir[PATH_MAX];
  memcpy(macos_dir, resolved, strlen(resolved) + 1);
  if (parent_dir(macos_dir) != 0) {
    fputs("Could not resolve Contents/MacOS directory.\n", stderr);
    return 127;
  }
  memcpy(contents_dir, macos_dir, strlen(macos_dir) + 1);
  if (parent_dir(contents_dir) != 0) {
    fputs("Could not resolve Contents directory.\n", stderr);
    return 127;
  }
  if (join_path(resources_dir, sizeof(resources_dir), contents_dir, "Resources") != 0) {
    fputs("Resources path is too long.\n", stderr);
    return 127;
  }

  char launcher[PATH_MAX];
  char bundled_python[PATH_MAX];
  if (join_path(launcher, sizeof(launcher), resources_dir, "launcher/mac_app_launcher.py") != 0 ||
      join_path(bundled_python, sizeof(bundled_python), resources_dir, "python/cpython-3.12/bin/python3.12") != 0) {
    fputs("Bundled launcher path is too long.\n", stderr);
    return 127;
  }

  setenv("TOTALSEGMENTATOR_WRAPPER_MAC_BUNDLE_RESOURCES_DIR", resources_dir, 1);
  setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
  set_app_support_python_cache();
  if (getenv("PATH") == NULL || getenv("PATH")[0] == '\0') {
    setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin", 1);
  }
  if (getenv("TMPDIR") == NULL || getenv("TMPDIR")[0] == '\0') {
    setenv("TMPDIR", "/tmp", 1);
  }

  const char *python = NULL;
  if (access(bundled_python, X_OK) == 0) {
    python = bundled_python;
  } else {
    const char *configured = getenv("TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312");
    if (configured != NULL && configured[0] != '\0' && access(configured, X_OK) == 0) {
      python = configured;
    }
  }
  if (python == NULL) {
    write_python_missing_state();
    fputs("TotalSegmentator Wrapper for Mac requires bundled Python 3.12 or TOTALSEGMENTATOR_WRAPPER_MAC_PYTHON_312.\n", stderr);
    return 2;
  }

  char **child_argv = calloc((size_t)argc + 3, sizeof(char *));
  if (child_argv == NULL) {
    perror("calloc");
    return 127;
  }
  child_argv[0] = (char *)python;
  child_argv[1] = "-B";
  child_argv[2] = launcher;
  for (int i = 1; i < argc; i++) {
    child_argv[i + 2] = argv[i];
  }
  child_argv[argc + 2] = NULL;

  execv(python, child_argv);
  perror("execv");
  return 127;
}
