# Kubernetes & OpenShift Troubleshooting Knowledge Base Standard

Each article should follow this template:

```yaml
id:
title:
alert_type:
severity:
automatable:
category:

detection_patterns:
  -

verification_commands:
  -

fix_commands:
  -

validation_commands:
  -

escalation_required:
```

---

# Article: Pod Stuck in Pending State

## Metadata

```yaml
id: KB-0001
title: Pod Stuck in Pending State
alert_type: POD_PENDING
severity: medium
automatable: partially
category: scheduling

detection_patterns:
  - Pending
  - FailedScheduling
  - insufficient memory
  - insufficient cpu
  - node selector mismatch
  - PVC pending

verification_commands:
  - kubectl describe pod
  - kubectl get pvc
  - kubectl get nodes

fix_commands:
  - kubectl patch deployment
  - kubectl scale
  - add tolerations

validation_commands:
  - kubectl get pods

escalation_required: false
```

## Overview

A Pod is in the `Pending` state when Kubernetes accepts the workload but cannot schedule it onto a node.

---

## Investigation

Describe the Pod:

```bash
kubectl describe pod myapp
```

Common event messages:

```text
0/3 nodes available: insufficient memory
```

```text
0/3 nodes available: node had taint
```

```text
persistentvolumeclaim is not bound
```

---

## Root Causes

### Insufficient CPU or Memory

Check cluster resources:

```bash
kubectl top nodes
```

### Node Selector Mismatch

Inspect deployment:

```bash
kubectl get deployment myapp -o yaml
```

Check node labels:

```bash
kubectl get nodes --show-labels
```

### Missing Toleration

Check taints:

```bash
kubectl describe node <node-name>
```

### PVC Not Bound

```bash
kubectl get pvc
```

---

## Automated Fix

### Remove Incorrect Node Selector

```bash
kubectl edit deployment myapp
```

or

```bash
kubectl patch deployment myapp \
--type=json \
-p='[
{
"op":"remove",
"path":"/spec/template/spec/nodeSelector"
}]'
```

### Scale Cluster

Infrastructure-specific action required.

### Fix PVC

Verify StorageClass:

```bash
kubectl get storageclass
```

---

## Validation

```bash
kubectl get pods
```

Expected:

```text
Running
```

---

## Escalation

Escalate if:

* No nodes have available resources
* Storage backend unavailable
* Scheduler repeatedly failing

---

# Article: Pod CrashLoopBackOff

## Metadata

```yaml
id: KB-0002
title: Pod CrashLoopBackOff
alert_type: CRASH_LOOP
severity: high
automatable: partially
category: workload

detection_patterns:
  - CrashLoopBackOff
  - Back-off restarting failed container

verification_commands:
  - kubectl logs
  - kubectl describe pod

fix_commands:
  - kubectl set env
  - kubectl patch deployment
  - kubectl rollout restart

validation_commands:
  - kubectl rollout status
  - kubectl get pods

escalation_required: false
```

## Overview

The container starts but repeatedly crashes.

---

## Investigation

```bash
kubectl logs myapp
```

Previous crash:

```bash
kubectl logs myapp --previous
```

Describe:

```bash
kubectl describe pod myapp
```

---

## Common Causes

### Invalid Configuration

### Missing Environment Variables

### Health Probe Failure

### Application Startup Error

### OOMKilled

See dedicated OOMKilled article.

---

## Automated Fixes

### Update Environment Variable

```bash
kubectl set env deployment/myapp \
DATABASE_HOST=db-service
```

### Update Image

```bash
kubectl set image deployment/myapp \
app=myrepo/myapp:v2.1
```

### Adjust Liveness Probe

```bash
kubectl edit deployment myapp
```

### Restart Rollout

```bash
kubectl rollout restart deployment/myapp
```

---

## Validation

```bash
kubectl rollout status deployment/myapp
```

```bash
kubectl get pods
```

---

## Escalation

Escalate if:

* Container crashes after configuration correction
* Application logs indicate code defects

---

# Article: Missing Secret or Incorrect Secret Name

## Metadata

```yaml
id: KB-0003
title: Missing Secret or Incorrect Secret Name
alert_type: MISSING_SECRET
severity: high
automatable: true
category: workload

detection_patterns:
  - secret not found
  - CreateContainerConfigError
  - Error: secret

verification_commands:
  - kubectl get secret
  - kubectl describe pod

fix_commands:
  - kubectl create secret generic
  - kubectl patch deployment
  - kubectl rollout restart

validation_commands:
  - kubectl rollout status
  - kubectl get pods

escalation_required: false
```

## Overview

A workload references a Secret that does not exist or has an incorrect name.

---

## Symptoms

```text
CreateContainerConfigError
```

---

## Investigation

```bash
kubectl describe pod myapp
```

Example:

```text
Error: secret "db-password" not found
```

Inspect deployment:

```bash
kubectl get deployment myapp -o yaml
```

---

## Verification

```bash
kubectl get secret db-password
```

List available secrets:

```bash
kubectl get secrets
```

---

## Automated Fix

### Create Missing Secret

```bash
kubectl create secret generic db-password \
--from-literal=password='MyPassword'
```

OpenShift:

```bash
oc create secret generic db-password \
--from-literal=password='MyPassword'
```

### Update Deployment Secret Reference

```bash
kubectl edit deployment myapp
```

or

```bash
kubectl patch deployment myapp \
--type=json \
-p='[
{
"op":"replace",
"path":"/spec/template/spec/volumes/0/secret/secretName",
"value":"new-secret"
}]'
```

### Restart Rollout

```bash
kubectl rollout restart deployment myapp
```

---

## Validation

```bash
kubectl rollout status deployment myapp
```

```bash
kubectl get pods
```

Expected:

```text
Running
```

---

## Escalation

Escalate if:

* Secret managed by Vault
* Secret managed by External Secrets Operator
* Secret immediately disappears after creation

---

# Article: ImagePullBackOff

## Metadata

```yaml
id: KB-0004
title: ImagePullBackOff
alert_type: IMAGE_PULL_BACKOFF
severity: high
automatable: true
category: registry

detection_patterns:
  - ImagePullBackOff
  - ErrImagePull
  - pull access denied
  - unauthorized
  - manifest unknown
  - not found

verification_commands:
  - kubectl describe pod
  - kubectl get secret
  - kubectl get sa default -o yaml

fix_commands:
  - kubectl set image deployment
  - kubectl create secret docker-registry
  - kubectl patch serviceaccount default

validation_commands:
  - kubectl get pods
  - kubectl rollout status

escalation_required: false
```

## Investigation

```bash
kubectl describe pod myapp
```

Common errors:

```text
pull access denied
```

```text
unauthorized
```

```text
manifest unknown
```

---

## Automated Fix

### Correct Image

```bash
kubectl set image deployment/myapp \
app=myregistry/myapp:v1.2
```

### Create Pull Secret

```bash
kubectl create secret docker-registry regcred \
--docker-server=myregistry.example.com \
--docker-username=user \
--docker-password=password
```

Attach:

```bash
kubectl patch serviceaccount default \
-p '{"imagePullSecrets":[{"name":"regcred"}]}'
```

---

## Validation

```bash
kubectl rollout restart deployment/myapp
```

```bash
kubectl get pods
```

---

# Article: PersistentVolumeClaim Pending

## Metadata

```yaml
id: KB-0005
title: PersistentVolumeClaim Pending
alert_type: PVC_PENDING
severity: high
automatable: partially
category: storage

detection_patterns:
  - PVC pending
  - persistentvolumeclaim is not bound
  - no persistent volumes available
  - storageclass not found
  - waiting for a volume to be created

verification_commands:
  - kubectl describe pvc
  - kubectl get storageclass
  - kubectl get pv
  - kubectl get pods -n openshift-cluster-csi-drivers

fix_commands:
  - kubectl edit pvc
  - kubectl apply -f pv.yaml
  - kubectl rollout restart deployment

validation_commands:
  - kubectl get pvc
  - kubectl get pods

escalation_required: true
```

## Investigation

```bash
kubectl describe pvc myclaim
```

```bash
kubectl get storageclass
```

```bash
kubectl get pv
```

---

## Automated Fix

### Correct StorageClass

```bash
kubectl edit pvc myclaim
```

### Create Persistent Volume

```bash
kubectl apply -f pv.yaml
```

### Restart Workload

```bash
kubectl rollout restart deployment/myapp
```

---

## Validation

```bash
kubectl get pvc
```

Expected:

```text
Bound
```

---

# Article: Node NotReady

## Metadata

```yaml
id: KB-0006
title: Node NotReady
alert_type: NODE_NOT_READY
severity: critical
automatable: partially
category: infrastructure

detection_patterns:
  - NotReady
  - node not ready
  - MemoryPressure
  - DiskPressure
  - PIDPressure
  - kubelet stopped posting node status

verification_commands:
  - kubectl describe node
  - kubectl get nodes
  - systemctl status kubelet
  - journalctl -u kubelet

fix_commands:
  - systemctl restart kubelet
  - crictl rmi --prune
  - df -h

validation_commands:
  - kubectl get nodes

escalation_required: true
```

## Investigation

```bash
kubectl describe node worker-1
```

Check:

```text
MemoryPressure
DiskPressure
Ready=False
```

Node:

```bash
systemctl status kubelet
```

Logs:

```bash
journalctl -u kubelet
```

---

## Automated Fix

### Restart Kubelet

```bash
systemctl restart kubelet
```

### Free Disk Space

```bash
df -h
```

Cleanup:

```bash
crictl rmi --prune
```

---

## Validation

```bash
kubectl get nodes
```

Expected:

```text
Ready
```

---

# Article: Service Not Accessible

## Metadata

```yaml
id: KB-0007
title: Service Not Accessible
alert_type: SERVICE_UNREACHABLE
severity: medium
automatable: true
category: networking

detection_patterns:
  - connection refused
  - no endpoints available
  - service unreachable
  - selector mismatch
  - endpoints not populated

verification_commands:
  - kubectl get svc
  - kubectl get endpoints
  - kubectl get pods --show-labels
  - kubectl get svc myservice -o yaml

fix_commands:
  - kubectl edit svc
  - kubectl rollout restart deployment

validation_commands:
  - kubectl get endpoints
  - kubectl get pods

escalation_required: false
```

## Investigation

```bash
kubectl get svc
```

```bash
kubectl get endpoints
```

```bash
kubectl get pods --show-labels
```

---

## Automated Fix

### Fix Selector

```bash
kubectl edit svc myservice
```

### Restart Deployment

```bash
kubectl rollout restart deployment/myapp
```

---

## Validation

```bash
kubectl get endpoints myservice
```

Endpoints should be populated.

---

# Article: OpenShift Route Not Working

## Metadata

```yaml
id: KB-0008
title: OpenShift Route Not Working
alert_type: ROUTE_UNAVAILABLE
severity: medium
automatable: partially
category: ingress

detection_patterns:
  - route not available
  - 503 Service Unavailable
  - router unavailable
  - TLS handshake error
  - certificate mismatch
  - no endpoints for route

verification_commands:
  - oc get routes
  - oc describe route
  - oc get pods -n openshift-ingress
  - oc get endpoints

fix_commands:
  - oc delete route
  - oc expose svc
  - oc rollout restart deployment/router-default -n openshift-ingress

validation_commands:
  - curl https://myroute.apps.cluster.example.com
  - oc get routes

escalation_required: false
```

## Investigation

```bash
oc get routes
```

```bash
oc describe route myroute
```

Check ingress router:

```bash
oc get pods -n openshift-ingress
```

---

## Automated Fix

### Recreate Route

```bash
oc delete route myroute
```

```bash
oc expose svc myservice
```

### Restart Router

```bash
oc rollout restart deployment/router-default \
-n openshift-ingress
```

---

## Validation

```bash
curl https://myroute.apps.cluster.example.com
```

---

# Article: Pod OOMKilled

## Metadata

```yaml
id: KB-0009
title: Pod OOMKilled
alert_type: OOMKILLED
severity: high
automatable: true
category: resources

detection_patterns:
  - OOMKilled
  - Exit Code 137
  - CrashLoopBackOff

verification_commands:
  - kubectl describe pod
  - kubectl top pod

fix_commands:
  - kubectl patch deployment
  - kubectl rollout restart

validation_commands:
  - kubectl top pods
  - kubectl get pods

escalation_required: false
```

## Overview

The Linux kernel terminates the container because it exceeds its memory limit.

---

## Investigation

```bash
kubectl describe pod myapp
```

Look for:

```text
Reason: OOMKilled
Exit Code: 137
```

Check usage:

```bash
kubectl top pod myapp
```

Inspect limits:

```bash
kubectl get deployment myapp -o yaml
```

---

## Automated Fix

### Increase Memory Limit

```bash
kubectl patch deployment myapp \
-p '{"spec":{"template":{"spec":{"containers":[{"name":"myapp","resources":{"limits":{"memory":"512Mi"}}}]}}}}'
```

OpenShift:

```bash
oc patch deployment myapp \
-p '{"spec":{"template":{"spec":{"containers":[{"name":"myapp","resources":{"limits":{"memory":"512Mi"}}}]}}}}'
```

### Restart Deployment

```bash
kubectl rollout restart deployment myapp
```

---

## Validation

```bash
kubectl top pods
```

```bash
kubectl get pods
```

Restart count should stop increasing.

---

## Escalation

Escalate if:

* OOM returns after doubling memory
* Memory usage grows continuously
* Multiple replicas exhibit the same pattern

Likely memory leak requiring application investigation.

---

# Article: High CPU Usage

## Metadata

```yaml
id: KB-0010
title: High CPU Usage
alert_type: HIGH_CPU
severity: medium
automatable: partially
category: resources

detection_patterns:
  - high cpu
  - cpu throttling
  - CPUThrottlingHigh
  - cpu limit exceeded
  - cpu request exceeded

verification_commands:
  - kubectl top nodes
  - kubectl top pods -A --sort-by=cpu
  - kubectl get deployment -o yaml

fix_commands:
  - kubectl patch deployment resources limits cpu
  - kubectl scale deployment --replicas

validation_commands:
  - kubectl top pods
  - kubectl get pods

escalation_required: false
```

## Investigation

```bash
kubectl top nodes
```

```bash
kubectl top pods -A --sort-by=cpu
```

---

## Automated Fix

Increase CPU limits:

```bash
kubectl patch deployment myapp \
-p '{"spec":{"template":{"spec":{"containers":[{"name":"myapp","resources":{"limits":{"cpu":"1000m"}}}]}}}}'
```

Scale application:

```bash
kubectl scale deployment myapp --replicas=5
```

---

## Validation

```bash
kubectl top pods
```

CPU utilization should stabilize below configured limits.

---

# Recommended RAG Chunking Strategy

Store each article as a separate document with metadata:

```yaml
source: kubernetes-troubleshooting
product:
  - Kubernetes
  - OpenShift

article_id: KB-0003
alert_type: MISSING_SECRET
severity: high
automatable: true
category: workload
```

