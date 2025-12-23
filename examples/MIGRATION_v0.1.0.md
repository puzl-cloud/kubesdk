# Migration Guide: Examples for kubesdk v0.1.0

This document describes the required changes to make the examples compatible with kubesdk v0.1.0.

## Summary of Breaking Changes

### 1. `path_picker` module removed

The `kubesdk.path_picker` module has been removed. Its exports are now available directly from `kubesdk`.

**Before:**
```python
from kubesdk.path_picker import from_root_, path_
```

**After:**
```python
from kubesdk import from_root_, path_
```

### 2. New `replace_()` function for deep updates

A new `replace_()` function simplifies deep nested updates on frozen dataclasses.

**Before (verbose nested replace):**
```python
from dataclasses import replace

old_container = original.spec.template.spec.containers[0]
new_container = replace(old_container, image=new_image)
new_containers = [new_container] + list(original.spec.template.spec.containers[1:])
new_pod_spec = replace(original.spec.template.spec, containers=new_containers)
new_template = replace(original.spec.template, spec=new_pod_spec)
new_spec = replace(original.spec, template=new_template)
modified = replace(original, spec=new_spec)
```

**After (using replace_):**
```python
from kubesdk import from_root_, path_, replace_

image_path = path_(from_root_(Deployment).spec.template.spec.containers[0].image)
modified = replace_(original, image_path, new_image)
```

### 3. More exports available from root module

Many types previously requiring deep imports are now exported from `kubesdk`:

```python
# These all work in v0.1.0:
from kubesdk import (
    # Client operations
    create_k8s_resource,
    get_k8s_resource,
    update_k8s_resource,
    delete_k8s_resource,
    create_or_update_k8s_resource,
    watch_k8s_resources,

    # Query types
    K8sQueryParams,
    QueryLabelSelector,
    QueryLabelSelectorRequirement,
    LabelSelectorOp,
    FieldSelector,
    FieldSelectorRequirement,
    FieldSelectorOp,

    # Watch types
    WatchEventType,
    K8sResourceEvent,

    # Path utilities
    from_root_,
    path_,
    replace_,
    PathPicker,
    PathRoot,

    # Errors
    NotFoundError,
    # ... other errors
)
```

## File-by-File Changes

### `03_deployments.py`

1. Update imports:
```diff
-from kubesdk.client import K8sQueryParams, QueryLabelSelector
-from kubesdk.path_picker import from_root_, path_
+from kubesdk import (
+    create_k8s_resource,
+    get_k8s_resource,
+    update_k8s_resource,
+    delete_k8s_resource,
+    NotFoundError,
+    K8sQueryParams,
+    QueryLabelSelector,
+    from_root_,
+    path_,
+    replace_,
+)
```

2. Simplify `update_container_image()`:
```diff
-    # Replace nested frozen dataclasses from innermost to outermost
-    old_container = original.spec.template.spec.containers[0]
-    new_container = replace(old_container, image=new_image)
-    new_containers = [new_container] + list(original.spec.template.spec.containers[1:])
-    new_pod_spec = replace(original.spec.template.spec, containers=new_containers)
-    new_template = replace(original.spec.template, spec=new_pod_spec)
-    new_spec = replace(original.spec, template=new_template)
-    modified = replace(original, spec=new_spec)
+    # Deep replace on frozen dataclass: spec.template.spec.containers[0].image = new_image
+    image_path = path_(from_root_(Deployment).spec.template.spec.containers[0].image)
+    modified = replace_(original, image_path, new_image)
```

### `04_watching.py`

Update imports (optional but recommended for consistency):
```diff
-from kubesdk.client import watch_k8s_resources, WatchEventType, K8sQueryParams, QueryLabelSelector
+from kubesdk import (
+    create_k8s_resource,
+    delete_k8s_resource,
+    NotFoundError,
+    watch_k8s_resources,
+    WatchEventType,
+    K8sQueryParams,
+    QueryLabelSelector,
+)
```

### `01_quickstart.py` and `02_authentication.py`

No changes required - these examples are already compatible with v0.1.0.
