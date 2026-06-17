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

// ssmAPI is the subset of the SSM client used by SSMTokenStore. Defined as an
// interface so tests can inject a fake without making network calls.
type ssmAPI interface {
	GetParameter(ctx context.Context, in *ssm.GetParameterInput, opts ...func(*ssm.Options)) (*ssm.GetParameterOutput, error)
	PutParameter(ctx context.Context, in *ssm.PutParameterInput, opts ...func(*ssm.Options)) (*ssm.PutParameterOutput, error)
}

// SSMTokenStore persists the rotating access/refresh token pair in AWS SSM
// Parameter Store as SecureString parameters under a given prefix:
//
//	<Prefix>/access   — main access token
//	<Prefix>/refresh  — main refresh token
//
// Standard-tier parameters are free and are encrypted with the AWS-managed
// aws/ssm KMS key. The required IAM permissions are ssm:GetParameter,
// ssm:PutParameter, and kms:Decrypt/kms:Encrypt on the aws/ssm key scoped to
// the <Prefix>/* path.
//
// SECURITY: token values must never appear in log output.
type SSMTokenStore struct {
	client ssmAPI
	prefix string // e.g. "/sb-collector"
}

// NewSSMTokenStore constructs an SSMTokenStore using the default AWS
// configuration (region from env/profile, credentials from instance role or
// environment).
//
// This constructor must only be called when the SSM_PARAM_PREFIX env var is
// set — it is never invoked in tests.
func NewSSMTokenStore(prefix string) (*SSMTokenStore, error) {
	cfg, err := config.LoadDefaultConfig(context.Background())
	if err != nil {
		return nil, fmt.Errorf("ssm token store: load aws config: %w", err)
	}
	return &SSMTokenStore{
		client: ssm.NewFromConfig(cfg),
		prefix: prefix,
	}, nil
}

// newSSMTokenStoreWithClient creates an SSMTokenStore with an injected client.
// Used in tests to avoid real AWS calls.
func newSSMTokenStoreWithClient(client ssmAPI, prefix string) *SSMTokenStore {
	return &SSMTokenStore{client: client, prefix: prefix}
}

// Load reads the access and refresh tokens from SSM. Returns ("", "", nil)
// when neither parameter exists yet (first boot, before any Save).
func (s *SSMTokenStore) Load() (access, refresh string, err error) {
	access, err = s.getParam(s.prefix + "/access")
	if err != nil {
		return "", "", fmt.Errorf("ssm load access token: %w", err)
	}
	refresh, err = s.getParam(s.prefix + "/refresh")
	if err != nil {
		return "", "", fmt.Errorf("ssm load refresh token: %w", err)
	}
	return access, refresh, nil
}

// Save writes both tokens to SSM. The refresh token is written first because
// it is restart-critical: a crash between the two PutParameter calls must never
// leave a stale refresh token in the store while a fresh one exists only in
// memory.
//
// SECURITY: token values must never appear in log output.
func (s *SSMTokenStore) Save(access, refresh string) error {
	// An empty value means "leave the stored token unchanged": SSM rejects
	// zero-length values, and overwriting a valid token with "" is data loss.
	// Write refresh first — restart-critical.
	if refresh != "" {
		if err := s.putParam(s.prefix+"/refresh", refresh); err != nil {
			return fmt.Errorf("ssm save refresh token: %w", err)
		}
	}
	if access != "" {
		if err := s.putParam(s.prefix+"/access", access); err != nil {
			return fmt.Errorf("ssm save access token: %w", err)
		}
	}
	return nil
}

// getParam fetches one SecureString parameter with decryption. Returns "" when
// the parameter does not exist (ParameterNotFound).
func (s *SSMTokenStore) getParam(name string) (string, error) {
	out, err := s.client.GetParameter(context.Background(), &ssm.GetParameterInput{
		Name:           aws.String(name),
		WithDecryption: aws.Bool(true),
	})
	if err != nil {
		// ParameterNotFound is expected on first boot — treat as empty.
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

// putParam writes one SecureString parameter with Overwrite=true.
func (s *SSMTokenStore) putParam(name, value string) error {
	_, err := s.client.PutParameter(context.Background(), &ssm.PutParameterInput{
		Name:      aws.String(name),
		Value:     aws.String(value),
		Type:      types.ParameterTypeSecureString,
		Overwrite: aws.Bool(true),
	})
	return err
}
