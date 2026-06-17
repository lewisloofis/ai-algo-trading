package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

// loadWatchlist resolves the symbol list from env or file, uppercases all
// entries, strips blank lines and # comments, and deduplicates.
//
// Priority:
//  1. CAPTURE_WATCHLIST env var (comma-separated)
//  2. CAPTURE_WATCHLIST_FILE env var (path to file, one symbol per line)
//
// At least one must be set and non-empty.
func loadWatchlist() ([]string, error) {
	if raw := os.Getenv("CAPTURE_WATCHLIST"); raw != "" {
		return parseInlineWatchlist(raw)
	}
	if path := os.Getenv("CAPTURE_WATCHLIST_FILE"); path != "" {
		return parseWatchlistFile(path)
	}
	return nil, fmt.Errorf("need CAPTURE_WATCHLIST (comma-separated) or CAPTURE_WATCHLIST_FILE (path)")
}

// parseInlineWatchlist splits a comma-separated string into deduplicated,
// uppercased, non-empty symbols.
func parseInlineWatchlist(raw string) ([]string, error) {
	parts := strings.Split(raw, ",")
	return dedupeSymbols(parts)
}

// parseWatchlistFile reads a file where each non-blank, non-comment line is a
// symbol. Lines starting with # (after optional leading whitespace) are
// ignored. Symbols are uppercased and deduplicated.
func parseWatchlistFile(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open watchlist file %q: %w", path, err)
	}
	defer f.Close()

	var lines []string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		lines = append(lines, sc.Text())
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("read watchlist file %q: %w", path, err)
	}
	return dedupeSymbols(lines)
}

// dedupeSymbols trims, uppercases, filters blank/comment lines, and
// deduplicates. Returns an error if the resulting list is empty.
func dedupeSymbols(raw []string) ([]string, error) {
	seen := make(map[string]struct{})
	var out []string
	for _, s := range raw {
		s = strings.TrimSpace(s)
		if s == "" || strings.HasPrefix(s, "#") {
			continue
		}
		s = strings.ToUpper(s)
		if _, dup := seen[s]; dup {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("watchlist is empty — provide at least one symbol")
	}
	return out, nil
}
