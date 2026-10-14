package recon

import (
	"encoding/json"
	"path/filepath"
)

// ReportPath is where the report for date (YYYY-MM-DD) lives under outDir:
// <outDir>/<date>.json, e.g. runs/recon/2026-10-14.json.
func ReportPath(outDir, date string) string { return filepath.Join(outDir, date+".json") }

// WriteReport writes r to path atomically, creating the directory. The report
// carries no token and no account number.
func WriteReport(path string, r Report) error {
	data, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(path, append(data, '\n'))
}
