/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package cert

import (
	"context"
	"fmt"
	"os"
	"strings"

	cert "github.com/open-policy-agent/cert-controller/pkg/rotator"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/rest"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
	"time"
)

const (
	caName         = "kubeagents-ca"
	caOrganization = "kubeagents"
)

// GetNamespace retrieves the namespace of the running operator
func GetNamespace() string {
	ns, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
	if err != nil {
		if envNS := os.Getenv("NAMESPACE"); envNS != "" {
			return envNS
		}
		return "kubeagents-system"
	}
	return strings.TrimSpace(string(ns))
}

// BootstrapCerts creates a minimal manager to generate certificates and inject CA bundles.
// This function blocks until certificates are ready and CA bundles are injected into Webhooks.
func BootstrapCerts(ctx context.Context, kubeConfig *rest.Config, certDir, secretName, serviceName, mutatingWebhookName, validatingWebhookName string) error {
	log := ctrl.Log.WithName("cert-bootstrap")

	log.Info("Creating bootstrap manager for certificate generation")
	bootstrapMgr, err := ctrl.NewManager(kubeConfig, ctrl.Options{
		Metrics: metricsserver.Options{
			BindAddress: "0",
		},
		HealthProbeBindAddress: "0",
	})
	if err != nil {
		return fmt.Errorf("unable to create bootstrap manager: %w", err)
	}

	if err := bootstrapMgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		return fmt.Errorf("unable to set up health check for bootstrap manager: %w", err)
	}

	certsReady := make(chan struct{})
	namespace := GetNamespace()

	dnsName := fmt.Sprintf("%s.%s.svc", serviceName, namespace)
	dnsNames := []string{
		dnsName,
		fmt.Sprintf("%s.%s.svc.cluster.local", serviceName, namespace),
	}

	rotatorConfig := &cert.CertRotator{
		SecretKey: types.NamespacedName{
			Namespace: namespace,
			Name:      secretName,
		},
		CertDir:        certDir,
		CAName:         caName,
		CAOrganization: caOrganization,
		DNSName:        dnsName,
		ExtraDNSNames:  dnsNames,
		IsReady:        certsReady,
		Webhooks: []cert.WebhookInfo{
			{Name: mutatingWebhookName, Type: cert.Mutating},
			{Name: validatingWebhookName, Type: cert.Validating},
		},
		RequireLeaderElection: false,
	}

	err = cert.AddRotator(bootstrapMgr, rotatorConfig)
	if err != nil {
		return fmt.Errorf("unable to add cert rotator to bootstrap manager: %w", err)
	}

	bootstrapCtx, bootstrapCancel := context.WithCancel(ctx)
	defer bootstrapCancel()

	// Since we are running locally, there is no kubelet to mount the secret to disk.
	// We run a background goroutine to poll the Secret from the API and write it to CertDir manually.
	go func() {
		ticker := time.NewTicker(1 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-bootstrapCtx.Done():
				return
			case <-ticker.C:
				var secret corev1.Secret
				err := bootstrapMgr.GetClient().Get(bootstrapCtx, types.NamespacedName{
					Namespace: namespace,
					Name:      secretName,
				}, &secret)
				if err == nil {
					certData, hasCert := secret.Data["tls.crt"]
					keyData, hasKey := secret.Data["tls.key"]
					if hasCert && hasKey && len(certData) > 0 && len(keyData) > 0 {
						_ = os.MkdirAll(certDir, 0755)
						_ = os.WriteFile(certDir+"/tls.crt", certData, 0600)
						_ = os.WriteFile(certDir+"/tls.key", keyData, 0600)
					}
				}
			}
		}
	}()

	managerStopped := make(chan struct{})
	go func() {
		log.Info("Starting bootstrap manager")
		if err := bootstrapMgr.Start(bootstrapCtx); err != nil {
			log.Error(err, "Bootstrap manager failed")
		}
		close(managerStopped)
	}()

	// Wait for cert-rotator to complete cert generation and CA injection
	log.Info("Waiting for certificate generation and CA injection to complete")
	select {
	case <-certsReady:
		log.Info("Certificates ready and CA bundles injected")
	case <-ctx.Done():
		return ctx.Err()
	}

	log.Info("Stopping bootstrap manager")
	bootstrapCancel()

	log.Info("Waiting for the bootstrap manager to stop")
	<-managerStopped

	log.Info("Certificate bootstrap complete")
	return nil
}

// ManageCerts adds the cert rotator to the main manager for ongoing certificate rotation.
func ManageCerts(mgr ctrl.Manager, certDir, secretName, serviceName, mutatingWebhookName, validatingWebhookName string) error {
	certsReady := make(chan struct{})
	namespace := GetNamespace()

	dnsName := fmt.Sprintf("%s.%s.svc", serviceName, namespace)
	dnsNames := []string{
		dnsName,
		fmt.Sprintf("%s.%s.svc.cluster.local", serviceName, namespace),
	}

	rotatorConfig := &cert.CertRotator{
		SecretKey: types.NamespacedName{
			Namespace: namespace,
			Name:      secretName,
		},
		CertDir:        certDir,
		CAName:         caName,
		CAOrganization: caOrganization,
		DNSName:        dnsName,
		ExtraDNSNames:  dnsNames,
		IsReady:        certsReady,
		Webhooks: []cert.WebhookInfo{
			{Name: mutatingWebhookName, Type: cert.Mutating},
			{Name: validatingWebhookName, Type: cert.Validating},
		},
		RequireLeaderElection: false,
	}

	if err := cert.AddRotator(mgr, rotatorConfig); err != nil {
		return fmt.Errorf("unable to add cert rotator to manager: %w", err)
	}

	return nil
}
