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

package controller

import (
	"context"
	"reflect"
	"testing"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
)

func TestBuildLiteLLMNetworkPolicy(t *testing.T) {
	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	profile := netpolProfile{
		Generated:          true,
		DNSClusterIPs:      []string{"10.96.0.10"},
		MetadataDaemonIP:   "169.254.169.252",
		MetadataDaemonPort: 988,
	}

	otlpEndpoint := "http://otel-collector.gke-managed-otel.svc:4318"
	netpol := buildLiteLLMNetworkPolicy(agent, profile, otlpEndpoint, false)

	if netpol.Name != "litellm-policy" {
		t.Errorf("expected name 'litellm-policy', got %q", netpol.Name)
	}
	if netpol.Namespace != "test-ns" {
		t.Errorf("expected namespace 'test-ns', got %q", netpol.Namespace)
	}
	if netpol.Labels[labelName] != "litellm" {
		t.Errorf("expected label %s='litellm', got %q", labelName, netpol.Labels[labelName])
	}
	if netpol.Spec.PodSelector.MatchLabels["app"] != "litellm" {
		t.Errorf("expected podSelector app='litellm', got %q", netpol.Spec.PodSelector.MatchLabels["app"])
	}

	// Verify policyTypes
	hasIngress := false
	hasEgress := false
	for _, pt := range netpol.Spec.PolicyTypes {
		if pt == networkingv1.PolicyTypeIngress {
			hasIngress = true
		}
		if pt == networkingv1.PolicyTypeEgress {
			hasEgress = true
		}
	}
	if !hasIngress || !hasEgress {
		t.Errorf("expected policyTypes to include Ingress and Egress, got %v", netpol.Spec.PolicyTypes)
	}

	// Verify Ingress rules:
	// Rule 1: same-namespace pods on 8080
	// Rule 2: gke-gmp-system on 8080
	if len(netpol.Spec.Ingress) != 2 {
		t.Fatalf("expected 2 ingress rules, got %d", len(netpol.Spec.Ingress))
	}
	ing0 := netpol.Spec.Ingress[0]
	if len(ing0.Ports) != 1 || ing0.Ports[0].Port.IntVal != 8080 {
		t.Errorf("expected ingress[0] port 8080, got %v", ing0.Ports)
	}
	if len(ing0.From) != 1 || ing0.From[0].PodSelector == nil || len(ing0.From[0].PodSelector.MatchLabels) != 0 {
		t.Errorf("expected ingress[0] from same-namespace pods (empty PodSelector), got %v", ing0.From)
	}

	ing1 := netpol.Spec.Ingress[1]
	if len(ing1.Ports) != 1 || ing1.Ports[0].Port.IntVal != 8080 {
		t.Errorf("expected ingress[1] port 8080, got %v", ing1.Ports)
	}
	if len(ing1.From) != 1 || ing1.From[0].NamespaceSelector == nil ||
		ing1.From[0].NamespaceSelector.MatchLabels["kubernetes.io/metadata.name"] != "gke-gmp-system" {
		t.Errorf("expected ingress[1] from gke-gmp-system, got %v", ing1.From)
	}

	// Verify Egress rules
	if len(netpol.Spec.Egress) != 5 {
		t.Fatalf("expected 5 egress rules, got %d", len(netpol.Spec.Egress))
	}

	// Egress 1: DNS
	dnsRule := netpol.Spec.Egress[0]
	has53UDP := false
	has53TCP := false
	for _, p := range dnsRule.Ports {
		if p.Port.IntVal == 53 && *p.Protocol == corev1.ProtocolUDP {
			has53UDP = true
		}
		if p.Port.IntVal == 53 && *p.Protocol == corev1.ProtocolTCP {
			has53TCP = true
		}
	}
	if !has53UDP || !has53TCP {
		t.Errorf("expected DNS rule to have port 53 UDP and TCP, got %v", dnsRule.Ports)
	}
	var dnsCIDRs []string
	for _, peer := range dnsRule.To {
		if peer.IPBlock != nil {
			dnsCIDRs = append(dnsCIDRs, peer.IPBlock.CIDR)
		}
	}
	expectedDNSCIDRs := []string{"169.254.20.10/32", "169.254.169.254/32", "10.96.0.10/32"}
	for _, expected := range expectedDNSCIDRs {
		found := false
		for _, c := range dnsCIDRs {
			if c == expected {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("expected DNS peer CIDR %s, got %v", expected, dnsCIDRs)
		}
	}

	// Egress 2: HTTPS 443 with 0.0.0.0/0 except RFC 1918
	httpsRule := netpol.Spec.Egress[1]
	if len(httpsRule.Ports) != 1 || httpsRule.Ports[0].Port.IntVal != 443 {
		t.Errorf("expected HTTPS rule port 443, got %v", httpsRule.Ports)
	}
	if len(httpsRule.To) != 1 || httpsRule.To[0].IPBlock == nil || httpsRule.To[0].IPBlock.CIDR != "0.0.0.0/0" {
		t.Errorf("expected HTTPS rule CIDR 0.0.0.0/0, got %v", httpsRule.To)
	}
	expectedExcept := []string{"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"}
	if !reflect.DeepEqual(httpsRule.To[0].IPBlock.Except, expectedExcept) {
		t.Errorf("expected HTTPS except %v, got %v", expectedExcept, httpsRule.To[0].IPBlock.Except)
	}

	// Egress 3: GCP Metadata Server (pre-NAT port 80 to 169.254.169.254/32)
	metaRule := netpol.Spec.Egress[2]
	if len(metaRule.Ports) != 1 || metaRule.Ports[0].Port.IntVal != 80 {
		t.Errorf("expected metadata rule port 80, got %v", metaRule.Ports)
	}
	if len(metaRule.To) != 1 || metaRule.To[0].IPBlock == nil || metaRule.To[0].IPBlock.CIDR != "169.254.169.254/32" {
		t.Errorf("expected metadata rule CIDR 169.254.169.254/32, got %v", metaRule.To)
	}

	// Egress 4: Workload Identity metadata daemon (port 988 to 169.254.169.254/32 and 169.254.169.252/32)
	daemonRule := netpol.Spec.Egress[3]
	if len(daemonRule.Ports) != 1 || daemonRule.Ports[0].Port.IntVal != 988 {
		t.Errorf("expected daemon rule port 988, got %v", daemonRule.Ports)
	}
	var daemonCIDRs []string
	for _, peer := range daemonRule.To {
		if peer.IPBlock != nil {
			daemonCIDRs = append(daemonCIDRs, peer.IPBlock.CIDR)
		}
	}
	expectedDaemonCIDRs := []string{"169.254.169.252/32", "169.254.169.254/32"}
	if !reflect.DeepEqual(daemonCIDRs, expectedDaemonCIDRs) {
		t.Errorf("expected daemon CIDRs %v, got %v", expectedDaemonCIDRs, daemonCIDRs)
	}

	// Egress 5: OTel Collector
	otelRule := netpol.Spec.Egress[4]
	has4317 := false
	has4318 := false
	for _, p := range otelRule.Ports {
		if p.Port.IntVal == 4317 {
			has4317 = true
		}
		if p.Port.IntVal == 4318 {
			has4318 = true
		}
	}
	if !has4317 || !has4318 {
		t.Errorf("expected OTel rule ports 4317 and 4318, got %v", otelRule.Ports)
	}
	if len(otelRule.To) != 1 || otelRule.To[0].NamespaceSelector == nil ||
		otelRule.To[0].NamespaceSelector.MatchLabels["kubernetes.io/metadata.name"] != "gke-managed-otel" {
		t.Errorf("expected OTel namespace selector gke-managed-otel, got %v", otelRule.To)
	}
}

// TestBuildLiteLLMNetworkPolicy_ResidualGapFix asserts that discovered custom RFC 1918
// DNS VIPs (e.g. 172.20.0.10) are included as explicit /32 CIDR peers, solving the
// residual gap where the static chart's 0.0.0.0/0 except 172.16.0.0/12 excluded them.
func TestBuildLiteLLMNetworkPolicy_ResidualGapFix(t *testing.T) {
	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	customDNSVIP := "172.20.0.10"
	profile := netpolProfile{
		Generated:     true,
		DNSClusterIPs: []string{customDNSVIP},
	}

	netpol := buildLiteLLMNetworkPolicy(agent, profile, "", true)
	dnsRule := netpol.Spec.Egress[0]

	foundCustomVIP := false
	for _, peer := range dnsRule.To {
		if peer.IPBlock != nil && peer.IPBlock.CIDR == customDNSVIP+"/32" {
			foundCustomVIP = true
			break
		}
	}
	if !foundCustomVIP {
		t.Fatalf("expected DNS rule to include discovered VIP %s/32 to resolve residual gap, but it was missing: %+v", customDNSVIP, dnsRule.To)
	}
}

func TestBuildLiteLLMNetworkPolicy_DualStack(t *testing.T) {
	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	profile := netpolProfile{
		Generated:     true,
		DNSClusterIPs: []string{"10.96.0.10", "2001:db8::10"},
	}

	netpol := buildLiteLLMNetworkPolicy(agent, profile, "", true)
	dnsRule := netpol.Spec.Egress[0]

	var cidrs []string
	for _, peer := range dnsRule.To {
		if peer.IPBlock != nil {
			cidrs = append(cidrs, peer.IPBlock.CIDR)
		}
	}

	hasIPv4 := false
	hasIPv6 := false
	for _, c := range cidrs {
		if c == "10.96.0.10/32" {
			hasIPv4 = true
		}
		if c == "2001:db8::10/128" {
			hasIPv6 = true
		}
	}
	if !hasIPv4 || !hasIPv6 {
		t.Errorf("expected dual-stack DNS peers 10.96.0.10/32 and 2001:db8::10/128, got %v", cidrs)
	}
}

func TestBuildLiteLLMNetworkPolicy_MetadataDaemonSuppressed(t *testing.T) {
	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	profile := netpolProfile{
		Generated:        true,
		DNSClusterIPs:    []string{"10.96.0.10"},
		MetadataDaemonIP: "", // suppressed
	}

	netpol := buildLiteLLMNetworkPolicy(agent, profile, "", true)

	// With metadata daemon suppressed and OTel disabled, there should be exactly 3 egress rules (DNS, HTTPS, Metadata-80).
	if len(netpol.Spec.Egress) != 3 {
		t.Fatalf("expected 3 egress rules when metadata daemon is suppressed, got %d", len(netpol.Spec.Egress))
	}
	for _, rule := range netpol.Spec.Egress {
		for _, p := range rule.Ports {
			if p.Port != nil && p.Port.IntVal == 988 {
				t.Errorf("found port 988 rule when metadata daemon should be suppressed")
			}
		}
	}
}

func TestBuildLiteLLMNetworkPolicy_OTelDisabled(t *testing.T) {
	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	profile := netpolProfile{
		Generated:        true,
		DNSClusterIPs:    []string{"10.96.0.10"},
		MetadataDaemonIP: "169.254.169.252",
	}

	netpol := buildLiteLLMNetworkPolicy(agent, profile, "http://otel.gke-managed-otel.svc:4318", true)

	for _, rule := range netpol.Spec.Egress {
		for _, p := range rule.Ports {
			if p.Port != nil && (p.Port.IntVal == 4317 || p.Port.IntVal == 4318) {
				t.Errorf("found OTel port %d when otlpDisabled is true", p.Port.IntVal)
			}
		}
	}
}

func TestReconcileLiteLLMNetworkPolicy_LiteLLMPresent(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	profile := netpolProfile{
		Generated:     true,
		DNSClusterIPs: []string{"10.96.0.10"},
	}

	ctx := context.Background()
	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err != nil {
		t.Fatalf("expected litellm-policy to exist, got error: %v", err)
	}

	// Anti-GC check: verify litellm-policy has NO OwnerReference to PlatformAgent
	if len(netpol.OwnerReferences) != 0 {
		t.Errorf("expected 0 OwnerReferences on litellm-policy for Anti-GC guarantee, got %d (%+v)",
			len(netpol.OwnerReferences), netpol.OwnerReferences)
	}

	// Verify labels stamped by applyManaged
	if netpol.Labels[labelManagedBy] != fieldOwner {
		t.Errorf("expected label %s=%s, got %q", labelManagedBy, fieldOwner, netpol.Labels[labelManagedBy])
	}
	if netpol.Labels[labelName] != "litellm" {
		t.Errorf("expected label %s='litellm', got %q", labelName, netpol.Labels[labelName])
	}
}

func TestReconcileLiteLLMNetworkPolicy_LiteLLMAbsent(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	// No litellm Deployment in the client
	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	profile := netpolProfile{
		Generated:     true,
		DNSClusterIPs: []string{"10.96.0.10"},
	}

	ctx := context.Background()
	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	// Verify litellm-policy was NOT created
	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err == nil {
		t.Fatalf("litellm-policy should not have been created when litellm Deployment is absent")
	}

	// If an existing managed litellm-policy exists and litellm is absent, it should be deleted
	existingManaged := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: fieldOwner,
			},
		},
	}
	if err := cl.Create(ctx, existingManaged); err != nil {
		t.Fatalf("failed to create existing managed policy: %v", err)
	}

	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err == nil {
		t.Fatalf("managed litellm-policy should have been deleted when litellm Deployment is absent")
	}
}

func TestReconcileLiteLLMNetworkPolicy_Disabled(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	existingManaged := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: fieldOwner,
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep, existingManaged).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	// Generated = false represents spec.networkPolicy.enabled == false
	profile := netpolProfile{
		Generated: false,
	}

	ctx := context.Background()
	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err == nil {
		t.Fatalf("managed litellm-policy should have been deleted when Generated is false")
	}
}

func TestReconcileLiteLLMNetworkPolicy_SafeDeletion_PreservesUnmanaged(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	// Existing litellm-policy managed by Helm, NOT the operator
	unmanagedPolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: "Helm",
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, unmanagedPolicy).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	// Generated = false
	profile := netpolProfile{
		Generated: false,
	}

	ctx := context.Background()
	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err != nil {
		t.Fatalf("unmanaged litellm-policy was deleted; expected it to be preserved: %v", err)
	}
}

func TestReconcileLiteLLMNetworkPolicy_AnnotationDisabled(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
			Annotations: map[string]string{
				AnnotationEnableLiteLLMNetworkPolicy: "false",
			},
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	existingManaged := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: fieldOwner,
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep, existingManaged).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	profile := netpolProfile{
		Generated: true,
	}

	ctx := context.Background()
	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err == nil {
		t.Fatalf("managed litellm-policy should have been deleted when annotation is false")
	}
}

func TestReconcileLiteLLMNetworkPolicy_AdoptionSkip_ExternalManaged(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	unmanagedPolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: "custom-tool",
			},
		},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{
				MatchLabels: map[string]string{"custom": "true"},
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep, unmanagedPolicy).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	profile := netpolProfile{
		Generated:     true,
		DNSClusterIPs: []string{"10.96.0.10"},
	}

	ctx := context.Background()
	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err != nil {
		t.Fatalf("failed to get litellm-policy: %v", err)
	}
	if netpol.Labels[labelManagedBy] != "custom-tool" {
		t.Errorf("expected managed-by label to remain 'custom-tool', got %q", netpol.Labels[labelManagedBy])
	}
	if netpol.Spec.PodSelector.MatchLabels["custom"] != "true" {
		t.Errorf("expected custom podSelector to remain untouched, got %v", netpol.Spec.PodSelector.MatchLabels)
	}
}

func TestReconcileLiteLLMNetworkPolicy_Adoption_LegacyKubeAgents(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "test-ns",
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	legacyPolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelPartOf:    partOfKubeAgents,
				labelManagedBy: managedByKustomize,
				labelName:      "litellm",
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep, legacyPolicy).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	profile := netpolProfile{
		Generated:     true,
		DNSClusterIPs: []string{"172.20.0.10"},
	}

	ctx := context.Background()
	if err := r.reconcileLiteLLMNetworkPolicy(ctx, agent, profile, "", true); err != nil {
		t.Fatalf("reconcileLiteLLMNetworkPolicy failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err != nil {
		t.Fatalf("failed to get adopted litellm-policy: %v", err)
	}
	hasDNSVIP := false
	for _, egress := range netpol.Spec.Egress {
		for _, peer := range egress.To {
			if peer.IPBlock != nil && peer.IPBlock.CIDR == "172.20.0.10/32" {
				hasDNSVIP = true
			}
		}
	}
	if !hasDNSVIP {
		t.Errorf("expected adopted litellm-policy to have discovered DNS VIP 172.20.0.10/32")
	}
}

func TestHandleDeletion_LiteLLMCleanup_DeploymentMissing(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:       "test-agent",
			Namespace:  "test-ns",
			Finalizers: []string{platformAgentFinalizer},
		},
	}

	managedPolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: fieldOwner,
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, managedPolicy).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	ctx := context.Background()
	if _, err := r.handleDeletion(ctx, agent); err != nil {
		t.Fatalf("handleDeletion failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err == nil {
		t.Fatalf("expected managed litellm-policy to be deleted when litellm Deployment is missing")
	}
}

func TestHandleDeletion_LiteLLMCleanup_DeploymentDeleting(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:       "test-agent",
			Namespace:  "test-ns",
			Finalizers: []string{platformAgentFinalizer},
		},
	}

	now := metav1.Now()
	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:              "litellm",
			Namespace:         "test-ns",
			DeletionTimestamp: &now,
			Finalizers:        []string{"some-test-finalizer"},
		},
	}

	managedPolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: fieldOwner,
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep, managedPolicy).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	ctx := context.Background()
	if _, err := r.handleDeletion(ctx, agent); err != nil {
		t.Fatalf("handleDeletion failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err == nil {
		t.Fatalf("expected managed litellm-policy to be deleted when litellm Deployment is being deleted")
	}
}

func TestHandleDeletion_LiteLLM_DeploymentActive_PreservesPolicy(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:       "test-agent",
			Namespace:  "test-ns",
			Finalizers: []string{platformAgentFinalizer},
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	managedPolicy := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm-policy",
			Namespace: "test-ns",
			Labels: map[string]string{
				labelManagedBy: fieldOwner,
			},
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep, managedPolicy).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	ctx := context.Background()
	if _, err := r.handleDeletion(ctx, agent); err != nil {
		t.Fatalf("handleDeletion failed: %v", err)
	}

	var netpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Namespace: "test-ns", Name: "litellm-policy"}, &netpol); err != nil {
		t.Fatalf("managed litellm-policy should be preserved when litellm Deployment is still active, got err: %v", err)
	}
}

func TestReconcile_Refusal_RuntimeClassNotFound_MaintainsNetworkGuardrails(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent-rc-missing",
			Namespace: "test-ns",
		},
		Spec: agentv1alpha1.PlatformAgentSpec{
			AgentSpec: agentv1alpha1.AgentSpec{
				Deployment: &agentv1alpha1.DeploymentSpec{
					Availability: &agentv1alpha1.AvailabilitySpec{
						RuntimeClassName: ptr.To("gvisor"),
					},
				},
			},
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep).
		WithStatusSubresource(&agentv1alpha1.PlatformAgent{}).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	req := ctrl.Request{
		NamespacedName: types.NamespacedName{
			Name:      "test-agent-rc-missing",
			Namespace: "test-ns",
		},
	}
	ctx := context.Background()

	// 1st Reconcile: adds finalizer
	if _, err := r.Reconcile(ctx, req); err != nil {
		t.Fatalf("Reconcile 1 failed: %v", err)
	}

	// 2nd Reconcile: encounters missing RuntimeClass, refuses workload but maintains guardrails
	res, err := r.Reconcile(ctx, req)
	if err != nil {
		t.Fatalf("Reconcile 2 failed: %v", err)
	}
	if res.RequeueAfter != 30*time.Second {
		t.Errorf("expected RequeueAfter 30s, got %v", res.RequeueAfter)
	}

	updated := &agentv1alpha1.PlatformAgent{}
	if err := cl.Get(ctx, req.NamespacedName, updated); err != nil {
		t.Fatalf("failed to get agent: %v", err)
	}
	if updated.Status.Phase != "Degraded" {
		t.Errorf("expected phase Degraded, got %q", updated.Status.Phase)
	}
	cond := meta.FindStatusCondition(updated.Status.Conditions, "Ready")
	if cond == nil || cond.Reason != reasonRuntimeClassNotFound {
		t.Errorf("expected Ready condition reason %s, got %v", reasonRuntimeClassNotFound, cond)
	}

	// Workload deployment must NOT be created
	dep := &appsv1.Deployment{}
	if err := cl.Get(ctx, types.NamespacedName{Name: "test-agent-rc-missing-gateway", Namespace: "test-ns"}, dep); !errors.IsNotFound(err) {
		t.Errorf("expected agent deployment to not be created, got err: %v", err)
	}

	// Network guardrails MUST be created/maintained
	var litellmNetpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Name: "litellm-policy", Namespace: "test-ns"}, &litellmNetpol); err != nil {
		t.Errorf("expected litellm-policy to be reconciled and present on RuntimeClassNotFound refusal, got err: %v", err)
	}

	var gatewayNetpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Name: "test-agent-rc-missing-gateway-netpol", Namespace: "test-ns"}, &gatewayNetpol); err != nil {
		t.Errorf("expected gateway-netpol to be reconciled and present on RuntimeClassNotFound refusal, got err: %v", err)
	}
}

func TestReconcile_Refusal_BrokerSplitStrandsEventWatcher_MaintainsNetworkGuardrails(t *testing.T) {
	scheme := setupScheme()

	agent := &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent-split-refused",
			Namespace: "test-ns",
		},
		Spec: agentv1alpha1.PlatformAgentSpec{
			Security: &agentv1alpha1.SecuritySpec{
				SplitCredentialBrokerPod: ptr.To(true),
			},
			Harness: &agentv1alpha1.HarnessSpec{
				EventWatcher: nil, // stock CR: event watcher is enabled by default
			},
		},
	}

	litellmDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "litellm",
			Namespace: "test-ns",
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(agent, litellmDep).
		WithStatusSubresource(&agentv1alpha1.PlatformAgent{}).
		WithInterceptorFuncs(fakeServerSideApplyInterceptors()).
		Build()

	r := &PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	req := ctrl.Request{
		NamespacedName: types.NamespacedName{
			Name:      "test-agent-split-refused",
			Namespace: "test-ns",
		},
	}
	ctx := context.Background()

	// 1st Reconcile: adds finalizer
	if _, err := r.Reconcile(ctx, req); err != nil {
		t.Fatalf("Reconcile 1 failed: %v", err)
	}

	// 2nd Reconcile: encounters split broker refusing event watcher, refuses workload but maintains guardrails
	res, err := r.Reconcile(ctx, req)
	if err != nil {
		t.Fatalf("Reconcile 2 failed: %v", err)
	}
	if res.RequeueAfter != 30*time.Second {
		t.Errorf("expected RequeueAfter 30s, got %v", res.RequeueAfter)
	}

	updated := &agentv1alpha1.PlatformAgent{}
	if err := cl.Get(ctx, req.NamespacedName, updated); err != nil {
		t.Fatalf("failed to get agent: %v", err)
	}
	if updated.Status.Phase != "Degraded" {
		t.Errorf("expected phase Degraded, got %q", updated.Status.Phase)
	}
	cond := meta.FindStatusCondition(updated.Status.Conditions, "Ready")
	if cond == nil || cond.Reason != reasonSplitBrokerStrandsEventWatcher {
		t.Errorf("expected Ready condition reason %s, got %v", reasonSplitBrokerStrandsEventWatcher, cond)
	}

	// Broker and agent deployments must NOT be created
	dep := &appsv1.Deployment{}
	if err := cl.Get(ctx, types.NamespacedName{Name: "test-agent-split-refused-gateway", Namespace: "test-ns"}, dep); !errors.IsNotFound(err) {
		t.Errorf("expected agent deployment to not be created, got err: %v", err)
	}
	brokerDep := &appsv1.Deployment{}
	if err := cl.Get(ctx, types.NamespacedName{Name: "test-agent-split-refused-credential-proxy", Namespace: "test-ns"}, brokerDep); !errors.IsNotFound(err) {
		t.Errorf("expected broker deployment to not be created, got err: %v", err)
	}

	// Network guardrails MUST be created/maintained
	var litellmNetpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Name: "litellm-policy", Namespace: "test-ns"}, &litellmNetpol); err != nil {
		t.Errorf("expected litellm-policy to be reconciled and present on broker split refusal, got err: %v", err)
	}

	var gatewayNetpol networkingv1.NetworkPolicy
	if err := cl.Get(ctx, types.NamespacedName{Name: "test-agent-split-refused-gateway-netpol", Namespace: "test-ns"}, &gatewayNetpol); err != nil {
		t.Errorf("expected gateway-netpol to be reconciled and present on broker split refusal, got err: %v", err)
	}
}

