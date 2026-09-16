package market

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// This file is the structural guarantee behind the package doc's claim that
// market sends no orders. It is the same device strategy/account uses: a
// test that fails the build if the property stops holding, rather than a
// comment asking reviewers to notice.

// TestPackageReferencesNoWritingHTTPMethod fails if any non-GET HTTP method
// constant appears anywhere in the package's non-test sources. A gate that
// could POST is no longer a gate.
func TestPackageReferencesNoWritingHTTPMethod(t *testing.T) {
	forbidden := []string{
		"MethodPost", "MethodPut", "MethodPatch", "MethodDelete",
		"MethodHead", "MethodConnect", "MethodOptions", "MethodTrace",
	}
	for _, file := range nonTestSources(t) {
		src, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read %s: %v", file, err)
		}
		for _, method := range forbidden {
			if strings.Contains(string(src), "http."+method) {
				t.Errorf("%s references http.%s — this package must stay read-only", filepath.Base(file), method)
			}
		}
	}
}

// TestSingleRequestConstructionPath fails if a second request-building call
// appears. One path means one place where the Authorization header, the
// user-agent and the read limit are applied — and one place to audit.
func TestSingleRequestConstructionPath(t *testing.T) {
	fset := token.NewFileSet()
	count := 0
	for _, file := range nonTestSources(t) {
		f, err := parser.ParseFile(fset, file, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", file, err)
		}
		ast.Inspect(f, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			pkg, ok := sel.X.(*ast.Ident)
			if !ok || pkg.Name != "http" {
				return true
			}
			if strings.HasPrefix(sel.Sel.Name, "NewRequest") {
				count++
			}
			return true
		})
	}
	if count != 1 {
		t.Errorf("found %d http.NewRequest* call sites, want exactly 1 (Client.get)", count)
	}
}

func nonTestSources(t *testing.T) []string {
	t.Helper()
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatalf("read package dir: %v", err)
	}
	var files []string
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		files = append(files, name)
	}
	if len(files) == 0 {
		t.Fatal("no non-test sources found — this test would pass vacuously")
	}
	return files
}
