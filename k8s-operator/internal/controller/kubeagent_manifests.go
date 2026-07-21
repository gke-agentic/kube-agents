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
	"crypto/sha256"
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/yaml"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
)

const defaultKubeAgentImage = "ghcr.io/gke-labs/kube-agents/kube-agent:latest"

// buildKubeAgentWorkspaceConfigMap generates the ConfigMap named <agent-name>-workspace-config.
func buildKubeAgentWorkspaceConfigMap(agent *agentv1alpha1.KubeAgent) *corev1.ConfigMap {
	content, _ := yaml.Marshal(agent.Spec)
	return &corev1.ConfigMap{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "v1",
			Kind:       "ConfigMap",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name + "-workspace-config",
			Namespace: agent.Namespace,
		},
		Data: map[string]string{
			"config.yaml": string(content),
		},
	}
}

// buildKubeAgentScheduleConfigMap generates the ConfigMap named <agent-name>-schedule-config.
func buildKubeAgentScheduleConfigMap(agent *agentv1alpha1.KubeAgent) *corev1.ConfigMap {
	schedulesData, _ := yaml.Marshal(agent.Spec.Schedules)
	return &corev1.ConfigMap{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "v1",
			Kind:       "ConfigMap",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name + "-schedule-config",
			Namespace: agent.Namespace,
		},
		Data: map[string]string{
			"schedules.yaml": string(schedulesData),
		},
	}
}

// buildKubeAgentPVC generates the PersistentVolumeClaim for agent storage.
func buildKubeAgentPVC(agent *agentv1alpha1.KubeAgent) *corev1.PersistentVolumeClaim {
	return &corev1.PersistentVolumeClaim{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "v1",
			Kind:       "PersistentVolumeClaim",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name + "-data",
			Namespace: agent.Namespace,
		},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes: []corev1.PersistentVolumeAccessMode{
				corev1.ReadWriteOnce,
			},
			Resources: corev1.VolumeResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceStorage: resource.MustParse("10Gi"),
				},
			},
		},
	}
}

// buildKubeAgentService generates the Service manifest for reaching the agent.
func buildKubeAgentService(agent *agentv1alpha1.KubeAgent) *corev1.Service {
	return &corev1.Service{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "v1",
			Kind:       "Service",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name,
			Namespace: agent.Namespace,
		},
		Spec: corev1.ServiceSpec{
			Selector: map[string]string{
				"app":                          agent.Name,
				"kubeagents.x-k8s.io/agent":    agent.Name,
				"kubeagents.x-k8s.io/resource": "kubeagent",
			},
			Ports: []corev1.ServicePort{
				{
					Name:       "http",
					Port:       8080,
					TargetPort: intstr.FromInt(8080),
					Protocol:   corev1.ProtocolTCP,
				},
			},
		},
	}
}

// hasClusterWidePermissions returns true if namespaces list contains "*"
func hasClusterWidePermissions(namespaces []string) bool {
	for _, ns := range namespaces {
		if ns == "*" {
			return true
		}
	}
	return false
}

// buildKubeAgentClusterRole generates a ClusterRole when cluster-wide permissions are needed.
func buildKubeAgentClusterRole(agent *agentv1alpha1.KubeAgent) *rbacv1.ClusterRole {
	roleName := fmt.Sprintf("kubeagents:kubeagent:%s:%s", agent.Namespace, agent.Name)
	return &rbacv1.ClusterRole{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "rbac.authorization.k8s.io/v1",
			Kind:       "ClusterRole",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name: roleName,
		},
		Rules: []rbacv1.PolicyRule{
			{
				APIGroups: []string{"*"},
				Resources: []string{"*"},
				Verbs:     []string{"*"},
			},
		},
	}
}

// buildKubeAgentClusterRoleBinding binds the agent ServiceAccount to the ClusterRole.
func buildKubeAgentClusterRoleBinding(agent *agentv1alpha1.KubeAgent) *rbacv1.ClusterRoleBinding {
	roleName := fmt.Sprintf("kubeagents:kubeagent:%s:%s", agent.Namespace, agent.Name)
	saName := agent.Name
	if agent.Spec.Security != nil && agent.Spec.Security.ServiceAccountName != "" {
		saName = agent.Spec.Security.ServiceAccountName
	}

	return &rbacv1.ClusterRoleBinding{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "rbac.authorization.k8s.io/v1",
			Kind:       "ClusterRoleBinding",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name: roleName,
		},
		Subjects: []rbacv1.Subject{
			{
				Kind:      "ServiceAccount",
				Name:      saName,
				Namespace: agent.Namespace,
			},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "ClusterRole",
			Name:     roleName,
		},
	}
}

// buildKubeAgentRole generates a Role in a target namespace.
func buildKubeAgentRole(agent *agentv1alpha1.KubeAgent, targetNs string) *rbacv1.Role {
	roleName := fmt.Sprintf("kubeagents:kubeagent:%s:%s", agent.Namespace, agent.Name)
	return &rbacv1.Role{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "rbac.authorization.k8s.io/v1",
			Kind:       "Role",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      roleName,
			Namespace: targetNs,
		},
		Rules: []rbacv1.PolicyRule{
			{
				APIGroups: []string{"*"},
				Resources: []string{"*"},
				Verbs:     []string{"*"},
			},
		},
	}
}

// buildKubeAgentRoleBinding binds the agent ServiceAccount to a Role in a target namespace.
func buildKubeAgentRoleBinding(agent *agentv1alpha1.KubeAgent, targetNs string) *rbacv1.RoleBinding {
	roleName := fmt.Sprintf("kubeagents:kubeagent:%s:%s", agent.Namespace, agent.Name)
	saName := agent.Name
	if agent.Spec.Security != nil && agent.Spec.Security.ServiceAccountName != "" {
		saName = agent.Spec.Security.ServiceAccountName
	}

	return &rbacv1.RoleBinding{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "rbac.authorization.k8s.io/v1",
			Kind:       "RoleBinding",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      roleName,
			Namespace: targetNs,
		},
		Subjects: []rbacv1.Subject{
			{
				Kind:      "ServiceAccount",
				Name:      saName,
				Namespace: agent.Namespace,
			},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "Role",
			Name:     roleName,
		},
	}
}

// buildKubeAgentDeployment generates the Deployment for the KubeAgent.
func buildKubeAgentDeployment(agent *agentv1alpha1.KubeAgent, workspaceHash, scheduleHash string) *appsv1.Deployment {
	labels := map[string]string{
		"app":                          agent.Name,
		"kubeagents.x-k8s.io/agent":    agent.Name,
		"kubeagents.x-k8s.io/resource": "kubeagent",
	}

	podAnnotations := map[string]string{
		"kubeagents.x-k8s.io/workspace-config-hash": workspaceHash,
		"kubeagents.x-k8s.io/schedule-config-hash":  scheduleHash,
	}
	if agent.Spec.Deployment != nil && len(agent.Spec.Deployment.PodAnnotations) > 0 {
		podAnnotations = mergeAnnotations(podAnnotations, agent.Spec.Deployment.PodAnnotations)
	}

	image := resolveAgentImage(agent.Spec.Deployment, defaultKubeAgentImage)
	replicas, strategy := resolveDeploymentReplicasAndStrategy(agent.Spec.Deployment)

	saName := agent.Name
	if agent.Spec.Security != nil && agent.Spec.Security.ServiceAccountName != "" {
		saName = agent.Spec.Security.ServiceAccountName
	}

	var envVars []corev1.EnvVar
	envVars = append(envVars, otelTelemetryEnvVars("kubeagent", agent.Name, agent.Namespace)...)
	envVars = append(envVars, corev1.EnvVar{
		Name:  "AGENT_NAME",
		Value: agent.Name,
	}, corev1.EnvVar{
		Name:  "AGENT_NAMESPACE",
		Value: agent.Namespace,
	})

	if agent.Spec.Deployment != nil && len(agent.Spec.Deployment.Env) > 0 {
		envVars = mergeEnvVars(envVars, agent.Spec.Deployment.Env)
	}

	volumes := []corev1.Volume{
		{
			Name: "agent-data",
			VolumeSource: corev1.VolumeSource{
				PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
					ClaimName: agent.Name + "-data",
				},
			},
		},
		{
			Name: "workspace-config",
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{
						Name: agent.Name + "-workspace-config",
					},
				},
			},
		},
		{
			Name: "schedule-config",
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{
						Name: agent.Name + "-schedule-config",
					},
				},
			},
		},
	}

	volumeMounts := []corev1.VolumeMount{
		{
			Name:      "agent-data",
			MountPath: "/opt/data",
		},
		{
			Name:      "workspace-config",
			MountPath: "/etc/kubeagents/workspace",
			ReadOnly:  true,
		},
		{
			Name:      "schedule-config",
			MountPath: "/etc/kubeagents/schedule",
			ReadOnly:  true,
		},
	}

	if agent.Spec.Deployment != nil {
		if len(agent.Spec.Deployment.ExtraVolumes) > 0 {
			volumes = append(volumes, agent.Spec.Deployment.ExtraVolumes...)
		}
		if len(agent.Spec.Deployment.ExtraVolumeMounts) > 0 {
			volumeMounts = append(volumeMounts, agent.Spec.Deployment.ExtraVolumeMounts...)
		}
	}

	var runtimeClassName *string
	if agent.Spec.Deployment != nil && agent.Spec.Deployment.RuntimeClassName != nil {
		runtimeClassName = agent.Spec.Deployment.RuntimeClassName
	}

	return &appsv1.Deployment{
		TypeMeta: metav1.TypeMeta{
			APIVersion: "apps/v1",
			Kind:       "Deployment",
		},
		ObjectMeta: metav1.ObjectMeta{
			Name:      agent.Name,
			Namespace: agent.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(replicas),
			Strategy: strategy,
			Selector: &metav1.LabelSelector{
				MatchLabels: labels,
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels:      labels,
					Annotations: podAnnotations,
				},
				Spec: corev1.PodSpec{
					ServiceAccountName: saName,
					RuntimeClassName:   runtimeClassName,
					Containers: []corev1.Container{
						{
							Name:            "agent",
							Image:           image,
							ImagePullPolicy: corev1.PullIfNotPresent,
							Env:             envVars,
							VolumeMounts:    volumeMounts,
						},
					},
					Volumes: volumes,
				},
			},
		},
	}
}

func hashConfigMapData(cm *corev1.ConfigMap) string {
	hasher := sha256.New()
	for k, v := range cm.Data {
		hasher.Write([]byte(k))
		hasher.Write([]byte(v))
	}
	return fmt.Sprintf("%x", hasher.Sum(nil))
}
