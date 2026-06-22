import re

file_path = "/usr/local/google/home/sergiuspiridon/kube-agents/k8s-operator/internal/controller/platformagent_manifests_test.go"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace the old HOME assertions with the new one
old_pattern = r'\tif _, ok := envMap\["HOME"\]; ok \{\s*t\.Errorf\("expected HOME to not be set in platform agent container Env"\)\s*\}\s*if !strings\.Contains\(container\.Args\[1\], `export HOME="/var/agent/home"`\) \{\s*t\.Errorf\("expected command to export HOME, got: %s", container\.Args\[1\]\)\s*\}'

# Let's do a simpler replacement based on the exact strings
target = """	if _, ok := envMap["HOME"]; ok {
		t.Errorf("expected HOME to not be set in platform agent container Env")
	}
	if !strings.Contains(container.Args[1], `export HOME="/var/agent/home"`) {
		t.Errorf("expected command to export HOME, got: %s", container.Args[1])
	}"""

replacement = """	if envMap["HOME"].Value != "/var/agent/home" {
		t.Errorf("expected HOME /var/agent/home, got %s", envMap["HOME"].Value)
	}"""

if target in content:
    content = content.replace(target, replacement)
    print("Found and replaced successfully using exact string!")
else:
    # Try with unix/windows newlines normalization
    content_normalized = content.replace("\r\n", "\n")
    if target in content_normalized:
        content_normalized = content_normalized.replace(target, replacement)
        content = content_normalized
        print("Found and replaced successfully after newline normalization!")
    else:
        # Fallback to regex
        content, count = re.subn(r'\t*if _, ok := envMap\["HOME"\]; ok \{[^\}]*\}\s*\t*if !strings\.Contains\(container\.Args\[1\], `export HOME="/var/agent/home"`\) \{[^\}]*\}', replacement, content)
        print(f"Regex replacement matches: {count}")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
