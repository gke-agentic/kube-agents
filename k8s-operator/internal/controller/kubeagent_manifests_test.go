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
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
)

func TestKubeAgentConfigMapNaming(t *testing.T) {
	agent := &agentv1alpha1.KubeAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "sre-agent",
			Namespace: "prod",
		},
		Spec: agentv1alpha1.KubeAgentSpec{
			Schedules: []agentv1alpha1.ScheduleReference{
				{Name: "hourly-check", Cron: "0 * * * *", TriggerPrompt: "Check alerts"},
			},
		},
	}

	workspaceCM := buildKubeAgentWorkspaceConfigMap(agent)
	if workspaceCM.Name != "sre-agent-workspace-config" {
		t.Errorf("expected sre-agent-workspace-config, got %s", workspaceCM.Name)
	}
	if _, ok := workspaceCM.Data["config.yaml"]; !ok {
		t.Errorf("expected config.yaml in workspace config map")
	}

	scheduleCM := buildKubeAgentScheduleConfigMap(agent)
	if scheduleCM.Name != "sre-agent-schedule-config" {
		t.Errorf("expected sre-agent-schedule-config, got %s", scheduleCM.Name)
	}
	if _, ok := scheduleCM.Data["schedules.yaml"]; !ok {
		t.Errorf("expected schedules.yaml in schedule config map")
	}
}

func TestKubeAgentRBACMultiNamespace(t *testing.T) {
	agent := &agentv1alpha1.KubeAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "sre-agent",
			Namespace: "agent-ns",
		},
		Spec: agentv1alpha1.KubeAgentSpec{
			Namespaces: []string{"apps-ns", "db-ns"},
		},
	}

	if hasClusterWidePermissions(agent.Spec.Namespaces) {
		t.Errorf("expected cluster-wide permissions to be false")
	}

	role := buildKubeAgentRole(agent, "apps-ns")
	if role.Namespace != "apps-ns" {
		t.Errorf("expected role in namespace apps-ns, got %s", role.Namespace)
	}

	rb := buildKubeAgentRoleBinding(agent, "apps-ns")
	if rb.Namespace != "apps-ns" {
		t.Errorf("expected rolebinding in namespace apps-ns, got %s", rb.Namespace)
	}
	if len(rb.Subjects) == 0 || rb.Subjects[0].Namespace != "agent-ns" {
		t.Errorf("expected subject namespace to be agent-ns")
	}

	clusterAgent := &agentv1alpha1.KubeAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cluster-agent",
			Namespace: "default",
		},
		Spec: agentv1alpha1.KubeAgentSpec{
			Namespaces: []string{"*"},
		},
	}
	if !hasClusterWidePermissions(clusterAgent.Spec.Namespaces) {
		t.Errorf("expected cluster-wide permissions to be true")
	}
}

func TestKubeAgentDeploymentAndService(t *testing.T) {
	agent := &agentv1alpha1.KubeAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-agent",
			Namespace: "default",
		},
	}

	dep := buildKubeAgentDeployment(agent, "hash1", "hash2")
	if dep.Name != "test-agent" {
		t.Errorf("expected deployment name test-agent, got %s", dep.Name)
	}
	if len(dep.Spec.Template.Spec.Containers) != 1 {
		t.Errorf("expected 1 container")
	}

	svc := buildKubeAgentService(agent)
	if svc.Name != "test-agent" {
		t.Errorf("expected service name test-agent, got %s", svc.Name)
	}
}
