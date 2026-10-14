package recon

import (
	"fmt"
	"path/filepath"
	"sort"

	"strategy/engine"
)

// ResolveStorePaths expands each pattern (a glob or a plain path), drops any
// file whose base name is in exclude, and returns the distinct paths sorted.
// A pattern that matches nothing contributes nothing: the first run of a day
// has no prior stores.
func ResolveStorePaths(patterns, exclude []string) ([]string, error) {
	skip := map[string]bool{}
	for _, e := range exclude {
		skip[e] = true
	}
	seen := map[string]bool{}
	var out []string
	for _, pat := range patterns {
		matches, err := filepath.Glob(pat)
		if err != nil {
			return nil, fmt.Errorf("store pattern %q: %w", pat, err)
		}
		for _, m := range matches {
			if skip[filepath.Base(m)] || seen[m] {
				continue
			}
			seen[m] = true
			out = append(out, m)
		}
	}
	sort.Strings(out)
	return out, nil
}

// LoadStoreFills reads sinkKind fills for account from every path. See
// engine.PostTradeStore.Fills for includeUnattributed. It returns the base
// names of the files read (for the report: a base name carries no account
// number) and the fills, oldest first.
func LoadStoreFills(paths []string, sinkKind, account string, includeUnattributed bool) ([]string, []engine.StoreFill, error) {
	var names []string
	var all []engine.StoreFill
	for _, p := range paths {
		s, err := engine.OpenStore(p)
		if err != nil {
			return nil, nil, fmt.Errorf("open store %s: %w", filepath.Base(p), err)
		}
		fills, err := s.Fills(sinkKind, account, includeUnattributed)
		closeErr := s.Close()
		if err != nil {
			return nil, nil, fmt.Errorf("read store %s: %w", filepath.Base(p), err)
		}
		if closeErr != nil {
			return nil, nil, fmt.Errorf("close store %s: %w", filepath.Base(p), closeErr)
		}
		names = append(names, filepath.Base(p))
		all = append(all, fills...)
	}
	sort.SliceStable(all, func(i, j int) bool { return fillLess(all[i], all[j]) })
	return names, all, nil
}
