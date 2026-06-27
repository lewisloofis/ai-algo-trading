package feed

import (
	"context"
	"errors"
	"fmt"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/ssm"
	"github.com/aws/aws-sdk-go-v2/service/ssm/types"
)

// SSMParamReader reads plain (String) parameters under a prefix from AWS SSM
// Parameter Store. Distinct from SSMTokenStore: non-secret runtime config (e.g.
// the watchlist) that the collector reads but never writes — Terraform stays
// authoritative for these values.
type SSMParamReader struct {
	client ssmAPI
	prefix string
}

// NewSSMParamReader constructs a reader using the default AWS config (region
// from env/profile, credentials from the instance role). Only call when
// SSM_PARAM_PREFIX is set.
func NewSSMParamReader(prefix string) (*SSMParamReader, error) {
	cfg, err := config.LoadDefaultConfig(context.Background())
	if err != nil {
		return nil, fmt.Errorf("ssm param reader: load aws config: %w", err)
	}
	return &SSMParamReader{client: ssm.NewFromConfig(cfg), prefix: prefix}, nil
}

// newSSMParamReaderWithClient injects a client for tests.
func newSSMParamReaderWithClient(client ssmAPI, prefix string) *SSMParamReader {
	return &SSMParamReader{client: client, prefix: prefix}
}

// Get reads "<prefix>/<name>". Returns "" (nil error) when the parameter does
// not exist, so callers can fall back to an env default.
func (r *SSMParamReader) Get(name string) (string, error) {
	out, err := r.client.GetParameter(context.Background(), &ssm.GetParameterInput{
		Name: aws.String(r.prefix + "/" + name),
	})
	if err != nil {
		var notFound *types.ParameterNotFound
		if errors.As(err, &notFound) {
			return "", nil
		}
		return "", err
	}
	if out.Parameter == nil || out.Parameter.Value == nil {
		return "", nil
	}
	return *out.Parameter.Value, nil
}

// GetSecure reads "<prefix>/<name>" as a SecureString (with KMS decryption).
// Returns "" (nil error) when the parameter does not exist, so callers can
// fall back to an env default.
func (r *SSMParamReader) GetSecure(name string) (string, error) {
	out, err := r.client.GetParameter(context.Background(), &ssm.GetParameterInput{
		Name:           aws.String(r.prefix + "/" + name),
		WithDecryption: aws.Bool(true),
	})
	if err != nil {
		var notFound *types.ParameterNotFound
		if errors.As(err, &notFound) {
			return "", nil
		}
		return "", err
	}
	if out.Parameter == nil || out.Parameter.Value == nil {
		return "", nil
	}
	return *out.Parameter.Value, nil
}
