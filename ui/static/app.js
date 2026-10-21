// CNC Milling Slicer — Subtractive Studio Version 1
let scene, camera, renderer, controls, transformControls;
let bedGrid, stockMesh, partMeshGroup, toolpathLinesGroup, toolMeshGroup;
let silhouetteOutlineGroup, featureEdgesGroup;
let currentMesh = null;
let currentSlicingData = null;
let currentModelType = "sample2";
let latestAnalysis = null;
let activeTool = "select"; // 'select', 'translate', 'rotate', 'scale', 'mirror'
let isSimulating = false;
let simIndex = 0;
let simPoints = [];
let simInterval = null;

// Initialize Three.js 3D Viewport
function initViewport() {
  const container = document.getElementById("viewport-3d");
  const width = container.clientWidth;
  const height = container.clientHeight;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x121214);

  // Perspective camera (Z is UP in CNC coordinates)
  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
  camera.position.set(160, -220, 180);
  camera.up.set(0, 0, 1);

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(window.devicePixelRatio);
  container.appendChild(renderer.domElement);

  // Orbit controls (Orbit / Pan / Zoom)
  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.target.set(80, 80, 15);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;

  // Transform Gizmo Controls (Creality Slicer 3D handles)
  transformControls = new THREE.TransformControls(camera, renderer.domElement);
  transformControls.size = 0.75;
  transformControls.space = "local";
  scene.add(transformControls);

  // Disable OrbitControls while dragging gizmo
  transformControls.addEventListener("dragging-changed", (event) => {
    controls.enabled = !event.value;
  });

  // Sync 3D Gizmo drags back to input drawer fields and move stock if locked
  transformControls.addEventListener("objectChange", () => {
    if (!currentMesh) return;
    syncMeshToUI();
    if (document.getElementById("stock-lock-to-model").checked) {
      syncStockToModelPosition();
    }
  });

  // Lighting
  const ambLight = new THREE.AmbientLight(0xffffff, 0.65);
  scene.add(ambLight);

  const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.85);
  dirLight1.position.set(200, 200, 300);
  scene.add(dirLight1);

  const dirLight2 = new THREE.DirectionalLight(0x90caf9, 0.45);
  dirLight2.position.set(-200, -200, 100);
  scene.add(dirLight2);

  // Spoilboard Bed (400 x 400 mm)
  createBedGrid(400, 400);

  // Groups
  partMeshGroup = new THREE.Group();
  toolpathLinesGroup = new THREE.Group();
  silhouetteOutlineGroup = new THREE.Group();
  featureEdgesGroup = new THREE.Group();

  scene.add(partMeshGroup);
  scene.add(toolpathLinesGroup);
  scene.add(silhouetteOutlineGroup);
  scene.add(featureEdgesGroup);

  // 3D Cutting Tool Mesh
  toolMeshGroup = createToolMesh(3.175);
  scene.add(toolMeshGroup);
  toolMeshGroup.visible = false;

  // Initial default stock block
  renderCurrentStockMesh();

  // Load default sample 3D pyramid
  loadSamplePyramid();

  // Resize handler
  window.addEventListener("resize", onWindowResize);

  // Drag-and-drop STL handler on canvas
  setupDragAndDrop(container);

  // Setup Creality tool listeners and analysis listeners
  setupCrealityControls();
  setupStockAndAnalysisControls();

  animate();
}

function onWindowResize() {
  const container = document.getElementById("viewport-3d");
  camera.aspect = container.clientWidth / container.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}

function createBedGrid(w, l) {
  if (bedGrid) scene.remove(bedGrid);

  const gridGroup = new THREE.Group();
  const plateGeo = new THREE.PlaneGeometry(w, l);
  const plateMat = new THREE.MeshStandardMaterial({
    color: 0x1e1e22,
    roughness: 0.9,
    metalness: 0.1
  });
  const plate = new THREE.Mesh(plateGeo, plateMat);
  plate.position.set(w / 2, l / 2, -0.2);
  gridGroup.add(plate);

  const grid = new THREE.GridHelper(w, 40, 0x44444c, 0x2a2a30);
  grid.rotation.x = Math.PI / 2;
  grid.position.set(w / 2, l / 2, 0);
  gridGroup.add(grid);

  const axes = new THREE.AxesHelper(50);
  gridGroup.add(axes);

  bedGrid = gridGroup;
  scene.add(bedGrid);
}

// 3D Cutting Tool representation (Spindle collet + Carbide endmill)
function createToolMesh(diameter = 3.175) {
  const group = new THREE.Group();

  // Carbide endmill shaft (silver cylinder)
  const bitGeo = new THREE.CylinderGeometry(diameter / 2, diameter / 2, 25, 16);
  bitGeo.rotateX(Math.PI / 2);
  const bitMat = new THREE.MeshStandardMaterial({ color: 0xe5e7eb, metalness: 0.9, roughness: 0.2 });
  const bit = new THREE.Mesh(bitGeo, bitMat);
  bit.position.set(0, 0, 12.5);
  group.add(bit);

  // ER11 Spindle Collet (dark collet cone)
  const colletGeo = new THREE.CylinderGeometry(10, 5, 20, 16);
  colletGeo.rotateX(Math.PI / 2);
  const colletMat = new THREE.MeshStandardMaterial({ color: 0x1f2937, metalness: 0.8, roughness: 0.5 });
  const collet = new THREE.Mesh(colletGeo, colletMat);
  collet.position.set(0, 0, 35);
  group.add(collet);

  return group;
}

// Crisp cylindrical wireframe silhouette (top ring, bottom ring, 4 quadrant ribs)
function createCylinderStockWireframe(radius, height) {
  const group = new THREE.Group();
  const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });

  const topPts = [];
  const botPts = [];
  const segments = 64;
  for (let i = 0; i <= segments; i++) {
    const theta = (i / segments) * Math.PI * 2;
    const x = radius * Math.cos(theta);
    const y = radius * Math.sin(theta);
    topPts.push(new THREE.Vector3(x, y, height / 2));
    botPts.push(new THREE.Vector3(x, y, -height / 2));
  }
  group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(topPts), lineMat));
  group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(botPts), lineMat));

  const ribPts = [];
  for (let i = 0; i < 4; i++) {
    const theta = (i / 4) * Math.PI * 2;
    const x = radius * Math.cos(theta);
    const y = radius * Math.sin(theta);
    ribPts.push(new THREE.Vector3(x, y, -height / 2));
    ribPts.push(new THREE.Vector3(x, y, height / 2));
  }
  group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(ribPts), lineMat));

  return group;
}

// Render Raw Stock Mesh (Box, Plate, Cylinder, Round Plate / Disc, Conforming)
function renderCurrentStockMesh() {
  const shape = document.getElementById("stock-shape") ? document.getElementById("stock-shape").value : "box";
  const w = parseFloat(document.getElementById("stock-w") ? document.getElementById("stock-w").value : 70);
  const l = parseFloat(document.getElementById("stock-l") ? document.getElementById("stock-l").value : 70);
  const t = parseFloat(document.getElementById("stock-t") ? document.getElementById("stock-t").value : 32);
  const dia = parseFloat(document.getElementById("stock-d") ? document.getElementById("stock-d").value : 90);

  let cx = 50 + w / 2;
  let cy = 50 + l / 2;

  const isLocked = document.getElementById("stock-lock-to-model") ? document.getElementById("stock-lock-to-model").checked : true;
  if (isLocked && currentMesh) {
    cx = currentMesh.position.x;
    cy = currentMesh.position.y;
  }

  updateStockMesh(shape, w, l, t, dia, cx, cy);
}

function updateStockMesh(shape, w, l, t, dia, cx, cy) {
  if (stockMesh) scene.remove(stockMesh);

  const mat = new THREE.MeshPhysicalMaterial({
    color: 0x10b981, // Translucent emerald
    transparent: true,
    opacity: 0.28,
    roughness: 0.3,
    transmission: 0.6,
    thickness: 1.0,
    depthWrite: false
  });

  const stockGroup = new THREE.Group();

  if (shape === "cylinder" || shape === "round_plate") {
    const r = dia / 2;
    const geo = new THREE.CylinderGeometry(r, r, t, 64);
    geo.rotateX(Math.PI / 2); // Orient cylinder upright with Z-axis
    const mesh = new THREE.Mesh(geo, mat);
    stockGroup.add(mesh);

    // Clean, crisp CAD cylindrical wireframe (top circle, bottom circle, 4 vertical ribs)
    const wireframe = createCylinderStockWireframe(r, t);
    stockGroup.add(wireframe);

    stockGroup.position.set(cx, cy, t / 2);
  } else if (shape === "conforming" && latestAnalysis && latestAnalysis.silhouette_exterior && latestAnalysis.silhouette_exterior.length > 2) {
    const shape2d = new THREE.Shape();
    latestAnalysis.silhouette_exterior.forEach((p, idx) => {
      if (idx === 0) shape2d.moveTo(p[0], p[1]);
      else shape2d.lineTo(p[0], p[1]);
    });
    shape2d.closePath();
    const geo = new THREE.ExtrudeGeometry(shape2d, { depth: t, bevelEnabled: false });
    const mesh = new THREE.Mesh(geo, mat);
    stockGroup.add(mesh);

    const edges = new THREE.EdgesGeometry(geo);
    const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });
    stockGroup.add(new THREE.LineSegments(edges, lineMat));

    stockGroup.position.set(cx, cy, 0);
  } else {
    // Box or Plate
    const geo = new THREE.BoxGeometry(w, l, t);
    const mesh = new THREE.Mesh(geo, mat);
    stockGroup.add(mesh);

    const edges = new THREE.EdgesGeometry(geo);
    const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });
    stockGroup.add(new THREE.LineSegments(edges, lineMat));

    stockGroup.position.set(cx, cy, t / 2);
  }

  stockMesh = stockGroup;
  scene.add(stockMesh);
}

// Keep stock and silhouette outline positions synchronized with model
function syncStockToModelPosition() {
  if (!currentMesh) return;
  const t = parseFloat(document.getElementById("stock-t").value || 30);
  const shape = document.getElementById("stock-shape").value;

  if (stockMesh) {
    if (shape === "conforming") {
      stockMesh.position.set(currentMesh.position.x, currentMesh.position.y, 0);
    } else {
      stockMesh.position.set(currentMesh.position.x, currentMesh.position.y, t / 2);
    }
  }

  if (silhouetteOutlineGroup) {
    silhouetteOutlineGroup.position.set(currentMesh.position.x, currentMesh.position.y, 0);
  }

  document.getElementById("stock-pos").value = `X: ${currentMesh.position.x.toFixed(1)}, Y: ${currentMesh.position.y.toFixed(1)}`;
}

// Load 3D Mesh into Scene
function display3DMesh(vertices, faces, metadata, analysis = null) {
  transformControls.detach();
  while (partMeshGroup.children.length > 0) {
    partMeshGroup.remove(partMeshGroup.children[0]);
  }

  const geometry = new THREE.BufferGeometry();
  const positions = [];
  const indices = [];

  for (let i = 0; i < vertices.length; i++) {
    positions.push(vertices[i][0], vertices[i][1], vertices[i][2]);
  }
  for (let i = 0; i < faces.length; i++) {
    indices.push(faces[i][0], faces[i][1], faces[i][2]);
  }

  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  const mat = new THREE.MeshStandardMaterial({
    color: 0xf59e0b, // Amber gold part
    roughness: 0.35,
    metalness: 0.65
  });

  const mesh = new THREE.Mesh(geometry, mat);

  // Position model centered on stock
  const stockW = parseFloat(document.getElementById("stock-w").value || 70);
  const stockL = parseFloat(document.getElementById("stock-l").value || 70);
  mesh.position.set(50 + stockW / 2, 50 + stockL / 2, 0);

  partMeshGroup.add(mesh);
  currentMesh = mesh;

  // Re-attach gizmo if currently in a transform tool
  if (activeTool !== "select" && activeTool !== "mirror") {
    transformControls.attach(currentMesh);
  }

  // Update UI metadata panel
  document.getElementById("model-name-badge").textContent = metadata.name || "3D Model";
  document.getElementById("model-dim-x").value = metadata.width.toFixed(1);
  document.getElementById("model-dim-y").value = metadata.length.toFixed(1);
  document.getElementById("model-dim-z").value = metadata.height.toFixed(1);
  document.getElementById("model-poly-count").value = metadata.faces_count || faces.length;

  // Reset transform inputs to identity
  resetTransformsUI();

  // Apply or run design analysis
  if (analysis) {
    applyAnalysisResult(analysis, true);
  } else {
    runDesignAnalysis(true);
  }

  document.getElementById("status-text").textContent = `Loaded 3D Model: ${metadata.name} (${metadata.width.toFixed(1)} x ${metadata.length.toFixed(1)} x ${metadata.height.toFixed(1)} mm)`;
}

// Reset UI transform fields
function resetTransformsUI() {
  document.getElementById("tf-pos-x").value = "0.0";
  document.getElementById("tf-pos-y").value = "0.0";
  document.getElementById("tf-pos-z").value = "0.0";
  document.getElementById("tf-rot-x").value = "0";
  document.getElementById("tf-rot-y").value = "0";
  document.getElementById("tf-rot-z").value = "0";
  document.getElementById("tf-scale-x").value = "100";
  document.getElementById("tf-scale-y").value = "100";
  document.getElementById("tf-scale-z").value = "100";
}

// Synchronize mesh transformations -> UI drawer fields
function syncMeshToUI() {
  if (!currentMesh) return;
  const stockW = parseFloat(document.getElementById("stock-w").value || 60);
  const stockL = parseFloat(document.getElementById("stock-l").value || 60);
  const baseX = 50 + stockW / 2;
  const baseY = 50 + stockL / 2;

  const px = currentMesh.position.x - baseX;
  const py = currentMesh.position.y - baseY;
  const pz = currentMesh.position.z;

  document.getElementById("tf-pos-x").value = px.toFixed(1);
  document.getElementById("tf-pos-y").value = py.toFixed(1);
  document.getElementById("tf-pos-z").value = pz.toFixed(1);

  // Convert radians to degrees
  const rx = THREE.MathUtils.radToDeg(currentMesh.rotation.x);
  const ry = THREE.MathUtils.radToDeg(currentMesh.rotation.y);
  const rz = THREE.MathUtils.radToDeg(currentMesh.rotation.z);

  document.getElementById("tf-rot-x").value = Math.round(rx % 360);
  document.getElementById("tf-rot-y").value = Math.round(ry % 360);
  document.getElementById("tf-rot-z").value = Math.round(rz % 360);

  // Scale in percentage
  document.getElementById("tf-scale-x").value = Math.round(currentMesh.scale.x * 100);
  document.getElementById("tf-scale-y").value = Math.round(currentMesh.scale.y * 100);
  document.getElementById("tf-scale-z").value = Math.round(currentMesh.scale.z * 100);
}

// Synchronize UI drawer fields -> mesh transformations
function syncUIToMesh() {
  if (!currentMesh) return;
  const stockW = parseFloat(document.getElementById("stock-w").value || 60);
  const stockL = parseFloat(document.getElementById("stock-l").value || 60);
  const baseX = 50 + stockW / 2;
  const baseY = 50 + stockL / 2;

  const px = parseFloat(document.getElementById("tf-pos-x").value || 0);
  const py = parseFloat(document.getElementById("tf-pos-y").value || 0);
  const pz = parseFloat(document.getElementById("tf-pos-z").value || 0);

  const rx = THREE.MathUtils.degToRad(parseFloat(document.getElementById("tf-rot-x").value || 0));
  const ry = THREE.MathUtils.degToRad(parseFloat(document.getElementById("tf-rot-y").value || 0));
  const rz = THREE.MathUtils.degToRad(parseFloat(document.getElementById("tf-rot-z").value || 0));

  const sx = parseFloat(document.getElementById("tf-scale-x").value || 100) / 100;
  const sy = parseFloat(document.getElementById("tf-scale-y").value || 100) / 100;
  const sz = parseFloat(document.getElementById("tf-scale-z").value || 100) / 100;

  currentMesh.position.set(baseX + px, baseY + py, pz);
  currentMesh.rotation.set(rx, ry, rz);
  currentMesh.scale.set(sx, sy, sz);

  if (document.getElementById("stock-lock-to-model").checked) {
    syncStockToModelPosition();
  }
}

// Creality Tool Selector & Drawer Management
function setActiveTool(tool) {
  activeTool = tool;
  const drawer = document.getElementById("transform-drawer");
  const drawerTitle = document.getElementById("drawer-title");
  const contentMove = document.getElementById("drawer-move-content");
  const contentRotate = document.getElementById("drawer-rotate-content");
  const contentScale = document.getElementById("drawer-scale-content");
  const contentMirror = document.getElementById("drawer-mirror-content");

  const buttons = {
    select: document.getElementById("tool-select-btn"),
    translate: document.getElementById("tool-translate-btn"),
    rotate: document.getElementById("tool-rotate-btn"),
    scale: document.getElementById("tool-scale-btn"),
    mirror: document.getElementById("tool-mirror-btn")
  };
  Object.values(buttons).forEach((b) => b && b.classList.remove("active"));
  if (buttons[tool]) buttons[tool].classList.add("active");

  contentMove.style.display = "none";
  contentRotate.style.display = "none";
  contentScale.style.display = "none";
  contentMirror.style.display = "none";

  if (tool === "select") {
    transformControls.detach();
    drawer.style.display = "none";
    document.getElementById("status-text").textContent = "View mode active. Orbit with left click, pan with right click, zoom with wheel.";
    return;
  }

  drawer.style.display = "block";

  if (tool === "translate") {
    drawerTitle.textContent = "Move Tool";
    contentMove.style.display = "flex";
    if (currentMesh) {
      transformControls.setMode("translate");
      transformControls.attach(currentMesh);
    }
  } else if (tool === "rotate") {
    drawerTitle.textContent = "Rotate Tool";
    contentRotate.style.display = "flex";
    if (currentMesh) {
      transformControls.setMode("rotate");
      transformControls.attach(currentMesh);
    }
  } else if (tool === "scale") {
    drawerTitle.textContent = "Scale Tool";
    contentScale.style.display = "flex";
    if (currentMesh) {
      transformControls.setMode("scale");
      transformControls.attach(currentMesh);
    }
  } else if (tool === "mirror") {
    drawerTitle.textContent = "Mirror Tool";
    contentMirror.style.display = "flex";
    transformControls.detach();
  }
}

// Lay Flat Face onto Bed Floor (Z=0)
async function layFlatModel() {
  document.getElementById("status-text").textContent = "Computing optimal flat face orientation (aligning to spoilboard Z=0)...";
  try {
    const res = await fetch("/api/model/layflat", { method: "POST" });
    const data = await res.json();
    if (data.success) {
      display3DMesh(data.vertices, data.faces, data.metadata);
      document.getElementById("status-text").textContent = "Model laid flat against spoilboard bed (Z=0).";
    } else {
      alert("Lay flat failed: " + (data.error || "Unknown error"));
    }
  } catch (err) {
    console.error("Lay flat error:", err);
  }
}

function centerModelInStock() {
  document.getElementById("tf-pos-x").value = "0.0";
  document.getElementById("tf-pos-y").value = "0.0";
  document.getElementById("tf-pos-z").value = "0.0";
  syncUIToMesh();
  document.getElementById("status-text").textContent = "Model centered in stock block.";
}

function dropModelToBed() {
  document.getElementById("tf-pos-z").value = "0.0";
  syncUIToMesh();
  document.getElementById("status-text").textContent = "Model dropped to bed floor (Z=0).";
}

function resetAllTransforms() {
  resetTransformsUI();
  syncUIToMesh();
  document.getElementById("status-text").textContent = "Model transformations reset to default.";
}

function addRotation(axis, degrees) {
  const inp = document.getElementById(`tf-rot-${axis}`);
  let val = (parseFloat(inp.value || 0) + degrees) % 360;
  if (val < 0) val += 360;
  inp.value = val;
  syncUIToMesh();
  runDesignAnalysis(false);
}

function autoFitModelIntoStock() {
  if (!currentMesh) return;
  const stockW = parseFloat(document.getElementById("stock-w").value || 60);
  const stockL = parseFloat(document.getElementById("stock-l").value || 60);
  const stockT = parseFloat(document.getElementById("stock-t").value || 30);
  const mX = parseFloat(document.getElementById("model-dim-x").value || 1);
  const mY = parseFloat(document.getElementById("model-dim-y").value || 1);
  const mZ = parseFloat(document.getElementById("model-dim-z").value || 1);

  const fitX = (stockW - 10) / mX;
  const fitY = (stockL - 10) / mY;
  const fitZ = (stockT - 2) / mZ;
  const bestRatio = Math.min(fitX, fitY, fitZ);
  const pct = Math.max(10, Math.floor(bestRatio * 100));

  document.getElementById("tf-scale-x").value = pct;
  document.getElementById("tf-scale-y").value = pct;
  document.getElementById("tf-scale-z").value = pct;
  syncUIToMesh();
  document.getElementById("status-text").textContent = `Auto-fit scaled model to ${pct}% to safely fit stock boundaries.`;
}

function mirrorAxis(axis) {
  if (!currentMesh) return;
  const inp = document.getElementById(`tf-scale-${axis}`);
  const curr = parseFloat(inp.value || 100);
  inp.value = -curr;
  syncUIToMesh();
  document.getElementById("status-text").textContent = `Mirrored model along ${axis.toUpperCase()} axis.`;
}

// Design Analysis & Outline Generation
async function runDesignAnalysis(autoSetStock = false) {
  document.getElementById("status-text").textContent = "Analyzing design geometry and extracting boundary outlines for meshing...";

  try {
    const payload = {
      rot_x: parseFloat(document.getElementById("tf-rot-x") ? document.getElementById("tf-rot-x").value : 0),
      rot_y: parseFloat(document.getElementById("tf-rot-y") ? document.getElementById("tf-rot-y").value : 0),
      rot_z: parseFloat(document.getElementById("tf-rot-z") ? document.getElementById("tf-rot-z").value : 0),
      scale_x: parseFloat(document.getElementById("tf-scale-x") ? document.getElementById("tf-scale-x").value : 100) / 100,
      scale_y: parseFloat(document.getElementById("tf-scale-y") ? document.getElementById("tf-scale-y").value : 100) / 100,
      scale_z: parseFloat(document.getElementById("tf-scale-z") ? document.getElementById("tf-scale-z").value : 100) / 100,
      margin_xy: 5.0,
      margin_z_top: 2.0
    };

    const res = await fetch("/api/model/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.success) return;

    applyAnalysisResult(data, autoSetStock);
  } catch (err) {
    console.error("Design analysis failed:", err);
  }
}

// Apply Analysis Results & Outlines
function applyAnalysisResult(data, autoSetStock = false) {
  latestAnalysis = data;

  // Update UI Stats
  document.getElementById("analysis-footprint").textContent = `${data.footprint_area_mm2.toFixed(0)} mm²`;
  document.getElementById("analysis-volume").textContent = `${data.volume_cm3.toFixed(1)} cm³`;
  document.getElementById("analysis-cyl-dia").textContent = `Ø${data.min_cylinder_diameter_mm.toFixed(1)} mm`;
  document.getElementById("analysis-feature-count").textContent = `${data.feature_edges.length} edges`;

  // Auto-adapt stock shape & dimensions if autoSetStock is requested
  if (autoSetStock && data.detected_shape) {
    const shapeSelect = document.getElementById("stock-shape");
    shapeSelect.value = data.detected_shape;
    shapeSelect.dispatchEvent(new Event("change"));

    if (data.detected_shape === "round_plate" || data.detected_shape === "cylinder") {
      document.getElementById("stock-d").value = data.recommended_cylinder.diameter;
      document.getElementById("stock-t").value = data.recommended_cylinder.thickness;
    } else if (data.detected_shape === "plate") {
      document.getElementById("stock-w").value = data.recommended_plate.width;
      document.getElementById("stock-l").value = data.recommended_plate.length;
      document.getElementById("stock-t").value = data.recommended_plate.thickness;
    } else {
      document.getElementById("stock-w").value = data.recommended_box.width;
      document.getElementById("stock-l").value = data.recommended_box.length;
      document.getElementById("stock-t").value = data.recommended_box.thickness;
    }
  }

  renderCurrentStockMesh();
  renderAnalysisOutlines(data);

  const desc = data.shape_description || "Design";
  document.getElementById("status-text").textContent = `Analysis: ${desc} detected (${data.footprint_area_mm2.toFixed(0)} mm², Ø${data.min_cylinder_diameter_mm.toFixed(1)}mm). Material silhouette fitted.`;
}

// Render 2D Footprint Silhouette & 3D Feature Edge Curves
function renderAnalysisOutlines(data) {
  while (silhouetteOutlineGroup.children.length > 0) {
    silhouetteOutlineGroup.remove(silhouetteOutlineGroup.children[0]);
  }
  while (featureEdgesGroup.children.length > 0) {
    featureEdgesGroup.remove(featureEdgesGroup.children[0]);
  }

  // 1. 2D Silhouette Footprint on Bed Floor
  if (data.silhouette_exterior && data.silhouette_exterior.length > 2) {
    const extPts = data.silhouette_exterior.map((p) => new THREE.Vector3(p[0], p[1], 0.2));
    const extGeo = new THREE.BufferGeometry().setFromPoints(extPts);
    const extMat = new THREE.LineBasicMaterial({ color: 0x00ffff, linewidth: 2 });
    const extLine = new THREE.LineLoop(extGeo, extMat);
    silhouetteOutlineGroup.add(extLine);
  }

  if (data.silhouette_interiors) {
    data.silhouette_interiors.forEach((ring) => {
      const intPts = ring.map((p) => new THREE.Vector3(p[0], p[1], 0.2));
      const intGeo = new THREE.BufferGeometry().setFromPoints(intPts);
      const intMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, linewidth: 1.5 });
      const intLine = new THREE.LineLoop(intGeo, intMat);
      silhouetteOutlineGroup.add(intLine);
    });
  }

  if (currentMesh) {
    silhouetteOutlineGroup.position.set(currentMesh.position.x, currentMesh.position.y, 0);
  }

  // 2. 3D Feature Edge Curves (creases, steps, sharp profile curves)
  if (data.feature_edges && data.feature_edges.length > 0) {
    const edgePts = [];
    data.feature_edges.forEach((segment) => {
      edgePts.push(new THREE.Vector3(segment[0][0], segment[0][1], segment[0][2]));
      edgePts.push(new THREE.Vector3(segment[1][0], segment[1][1], segment[1][2]));
    });

    const edgesGeo = new THREE.BufferGeometry().setFromPoints(edgePts);
    const edgesMat = new THREE.LineBasicMaterial({ color: 0xfbbf24, linewidth: 2 }); // Rich gold
    const edgesLines = new THREE.LineSegments(edgesGeo, edgesMat);
    featureEdgesGroup.add(edgesLines);
  }

  if (currentMesh) {
    featureEdgesGroup.position.copy(currentMesh.position);
    featureEdgesGroup.rotation.copy(currentMesh.rotation);
    featureEdgesGroup.scale.copy(currentMesh.scale);
  }

  silhouetteOutlineGroup.visible = document.getElementById("chk-show-silhouette").checked;
  featureEdgesGroup.visible = document.getElementById("chk-show-feature-edges").checked;
}

// Auto-Fit Raw Stock Dimensions around current design
function autoFitStockToDesign() {
  if (!latestAnalysis) {
    runDesignAnalysis(true);
    return;
  }

  const shape = document.getElementById("stock-shape").value;
  if (shape === "cylinder" || shape === "round_plate") {
    document.getElementById("stock-d").value = latestAnalysis.recommended_cylinder.diameter;
    document.getElementById("stock-t").value = latestAnalysis.recommended_cylinder.thickness;
  } else if (shape === "plate") {
    document.getElementById("stock-w").value = latestAnalysis.recommended_plate.width;
    document.getElementById("stock-l").value = latestAnalysis.recommended_plate.length;
    document.getElementById("stock-t").value = latestAnalysis.recommended_plate.thickness;
  } else {
    document.getElementById("stock-w").value = latestAnalysis.recommended_box.width;
    document.getElementById("stock-l").value = latestAnalysis.recommended_box.length;
    document.getElementById("stock-t").value = latestAnalysis.recommended_box.thickness;
  }

  renderCurrentStockMesh();
  document.getElementById("status-text").textContent = `Stock auto-fitted to design boundaries with machining margins.`;
}

// Setup Stock and Analysis Event Listeners
function setupStockAndAnalysisControls() {
  const shapeSelect = document.getElementById("stock-shape");
  const boxDims = document.getElementById("stock-box-dims");
  const cylDims = document.getElementById("stock-cylinder-dims");
  const conformingDims = document.getElementById("stock-conforming-dims");

  shapeSelect.addEventListener("change", () => {
    const val = shapeSelect.value;
    boxDims.style.display = (val === "box" || val === "plate") ? "block" : "none";
    cylDims.style.display = (val === "cylinder" || val === "round_plate") ? "block" : "none";
    conformingDims.style.display = (val === "conforming") ? "block" : "none";

    // Auto-update dimensions when shape changes if analysis exists
    if (latestAnalysis) {
      if ((val === "cylinder" || val === "round_plate") && latestAnalysis.recommended_cylinder) {
        document.getElementById("stock-d").value = latestAnalysis.recommended_cylinder.diameter;
        document.getElementById("stock-t").value = latestAnalysis.recommended_cylinder.thickness;
      } else if (val === "plate" && latestAnalysis.recommended_plate) {
        document.getElementById("stock-w").value = latestAnalysis.recommended_plate.width;
        document.getElementById("stock-l").value = latestAnalysis.recommended_plate.length;
        document.getElementById("stock-t").value = latestAnalysis.recommended_plate.thickness;
      } else if (val === "box" && latestAnalysis.recommended_box) {
        document.getElementById("stock-w").value = latestAnalysis.recommended_box.width;
        document.getElementById("stock-l").value = latestAnalysis.recommended_box.length;
        document.getElementById("stock-t").value = latestAnalysis.recommended_box.thickness;
      }
    }

    renderCurrentStockMesh();
  });

  // Re-render stock on dimension input changes
  ["stock-w", "stock-l", "stock-t", "stock-d"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", renderCurrentStockMesh);
  });

  // Auto-fit & Center stock buttons
  document.getElementById("btn-fit-stock-to-design").addEventListener("click", autoFitStockToDesign);
  document.getElementById("btn-center-stock-model").addEventListener("click", () => {
    if (currentMesh) {
      syncStockToModelPosition();
      document.getElementById("status-text").textContent = "Stock centered directly onto 3D model.";
    }
  });

  // Analysis run & preset buttons
  document.getElementById("btn-run-analysis").addEventListener("click", () => runDesignAnalysis(false));
  const btnAnalyzeHeader = document.getElementById("btn-analyze-model");
  if (btnAnalyzeHeader) btnAnalyzeHeader.addEventListener("click", () => runDesignAnalysis(false));

  document.getElementById("btn-apply-rec-box").addEventListener("click", () => {
    shapeSelect.value = "box";
    shapeSelect.dispatchEvent(new Event("change"));
  });

  document.getElementById("btn-apply-rec-cylinder").addEventListener("click", () => {
    shapeSelect.value = "round_plate";
    shapeSelect.dispatchEvent(new Event("change"));
  });

  document.getElementById("btn-apply-rec-plate").addEventListener("click", () => {
    shapeSelect.value = "plate";
    shapeSelect.dispatchEvent(new Event("change"));
  });

  // Outline visibility toggles
  document.getElementById("chk-show-silhouette").addEventListener("change", (e) => {
    silhouetteOutlineGroup.visible = e.target.checked;
  });
  document.getElementById("chk-show-feature-edges").addEventListener("change", (e) => {
    featureEdgesGroup.visible = e.target.checked;
  });
}

// Setup Creality Toolbar, Drawer, and Transform Event Listeners
function setupCrealityControls() {
  document.getElementById("tool-select-btn").addEventListener("click", () => setActiveTool("select"));
  document.getElementById("tool-translate-btn").addEventListener("click", () => setActiveTool("translate"));
  document.getElementById("tool-rotate-btn").addEventListener("click", () => setActiveTool("rotate"));
  document.getElementById("tool-scale-btn").addEventListener("click", () => setActiveTool("scale"));
  document.getElementById("tool-mirror-btn").addEventListener("click", () => setActiveTool("mirror"));
  document.getElementById("tool-layflat-btn").addEventListener("click", layFlatModel);
  document.getElementById("tool-center-btn").addEventListener("click", centerModelInStock);
  document.getElementById("tool-reset-btn").addEventListener("click", resetAllTransforms);

  document.getElementById("drawer-close").addEventListener("click", () => setActiveTool("select"));

  document.getElementById("btn-drawer-center").addEventListener("click", centerModelInStock);
  document.getElementById("btn-drawer-drop").addEventListener("click", dropModelToBed);

  document.getElementById("btn-rot-x-90").addEventListener("click", () => addRotation("x", 90));
  document.getElementById("btn-rot-y-90").addEventListener("click", () => addRotation("y", 90));
  document.getElementById("btn-rot-z-90").addEventListener("click", () => addRotation("z", 90));
  document.getElementById("btn-drawer-layflat").addEventListener("click", layFlatModel);
  document.getElementById("btn-drawer-rot-reset").addEventListener("click", () => {
    document.getElementById("tf-rot-x").value = "0";
    document.getElementById("tf-rot-y").value = "0";
    document.getElementById("tf-rot-z").value = "0";
    syncUIToMesh();
    runDesignAnalysis(false);
  });

  const uniformChk = document.getElementById("tf-scale-uniform");
  ["x", "y", "z"].forEach((axis) => {
    const input = document.getElementById(`tf-scale-${axis}`);
    input.addEventListener("input", (e) => {
      if (uniformChk.checked) {
        ["x", "y", "z"].forEach((otherAxis) => {
          if (otherAxis !== axis) {
            document.getElementById(`tf-scale-${otherAxis}`).value = e.target.value;
          }
        });
      }
      syncUIToMesh();
    });
    input.addEventListener("change", () => runDesignAnalysis(false));
  });

  document.getElementById("btn-drawer-fit").addEventListener("click", autoFitModelIntoStock);
  document.getElementById("btn-drawer-scale-reset").addEventListener("click", () => {
    document.getElementById("tf-scale-x").value = "100";
    document.getElementById("tf-scale-y").value = "100";
    document.getElementById("tf-scale-z").value = "100";
    syncUIToMesh();
    runDesignAnalysis(false);
  });

  document.getElementById("btn-mirror-x").addEventListener("click", () => mirrorAxis("x"));
  document.getElementById("btn-mirror-y").addEventListener("click", () => mirrorAxis("y"));
  document.getElementById("btn-mirror-z").addEventListener("click", () => mirrorAxis("z"));

  ["tf-pos-x", "tf-pos-y", "tf-pos-z", "tf-rot-x", "tf-rot-y", "tf-rot-z"].forEach((id) => {
    document.getElementById(id).addEventListener("input", syncUIToMesh);
  });

  const tabsCountSelect = document.getElementById("tabs-count");
  const tabsSpecsRow = document.getElementById("tabs-specs-row");
  tabsCountSelect.addEventListener("change", () => {
    const hasTabs = parseInt(tabsCountSelect.value) > 0;
    tabsSpecsRow.style.opacity = hasTabs ? "1" : "0.4";
    tabsSpecsRow.style.pointerEvents = hasTabs ? "auto" : "none";
  });
  tabsSpecsRow.style.opacity = "0.4";
  tabsSpecsRow.style.pointerEvents = "none";

  window.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (e.key === "Escape") setActiveTool("select");
    else if (e.key === "w" || e.key === "W") setActiveTool("translate");
    else if (e.key === "e" || e.key === "E") setActiveTool("rotate");
    else if (e.key === "r" || e.key === "R") setActiveTool("scale");
    else if (e.key === "m" || e.key === "M") setActiveTool("mirror");
  });
}

// Load Sample 2 (Stepped Pyramid 3D model)
async function loadSamplePyramid() {
  currentModelType = "sample2";
  try {
    const res = await fetch("/api/model/sample_pyramid");
    const data = await res.json();
    if (data.success) {
      document.getElementById("stock-w").value = 70;
      document.getElementById("stock-l").value = 70;
      document.getElementById("stock-t").value = 32;
      document.getElementById("stock-d").value = 90;
      display3DMesh(data.vertices, data.faces, data.metadata, data.analysis);
    }
  } catch (err) {
    console.error("Failed to load sample pyramid:", err);
  }
}

// Upload & Insert Arbitrary STL / OBJ
async function uploadStlFile(file) {
  currentModelType = "custom_stl";
  const formData = new FormData();
  formData.append("file", file);

  document.getElementById("status-text").textContent = `Parsing 3D Model: ${file.name}...`;

  try {
    const res = await fetch("/api/model/upload_stl", {
      method: "POST",
      body: formData
    });
    const data = await res.json();

    if (data.error) {
      alert("Error loading 3D model: " + data.error);
      return;
    }

    display3DMesh(data.vertices, data.faces, data.metadata, data.analysis);
  } catch (err) {
    console.error("Upload error:", err);
    alert("Upload failed. See console.");
  }
}

function setupDragAndDrop(container) {
  container.addEventListener("dragover", (e) => {
    e.preventDefault();
    container.style.border = "2px dashed #007acc";
  });
  container.addEventListener("dragleave", (e) => {
    e.preventDefault();
    container.style.border = "none";
  });
  container.addEventListener("drop", (e) => {
    e.preventDefault();
    container.style.border = "none";
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadStlFile(e.dataTransfer.files[0]);
    }
  });
}

// Trigger Toolhead LiDAR Auto-Scan
async function autoScanBedLidar() {
  const badge = document.getElementById("lidar-status-badge");
  if (badge) {
    badge.textContent = "Scanning Bed...";
    badge.style.background = "#7e22ce";
  }

  document.getElementById("status-text").textContent = "Toolhead LiDAR sweeping machine bed to locate stock coordinates & surface flatness...";

  try {
    const res = await fetch("/api/lidar/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bed_w: 400,
        bed_l: 400,
        stock_x: 50,
        stock_y: 50,
        stock_w: parseFloat(document.getElementById("stock-w").value),
        stock_l: parseFloat(document.getElementById("stock-l").value),
        stock_t: parseFloat(document.getElementById("stock-t").value)
      })
    });
    const data = await res.json();

    if (data.detected) {
      document.getElementById("stock-pos").value = `X: ${data.x_min.toFixed(1)}, Y: ${data.y_min.toFixed(1)}`;
      document.getElementById("surface-warp-val").textContent = `${data.max_deviation.toFixed(2)} mm (Auto-Leveled)`;

      renderCurrentStockMesh();
      if (badge) {
        badge.textContent = "Stock Located (Auto)";
        badge.style.background = "#065f46";
      }
      document.getElementById("status-text").textContent = `LiDAR Detection: Located ${data.width.toFixed(1)} x ${data.length.toFixed(1)} x ${data.thickness.toFixed(1)} mm stock. Auto-leveling active.`;
    }
  } catch (err) {
    console.error("Scan error:", err);
  }
}

// Slicing Core with Stock Geometry and Position Locking
async function sliceCurrentModel() {
  const sliceBtn = document.getElementById("btn-slice");
  sliceBtn.disabled = true;
  sliceBtn.textContent = "Slicing...";
  document.getElementById("status-text").textContent = "Calculating Subtractive Slices: Stock(Z) - Part(Z)...";

  try {
    const payload = {
      model_type: currentModelType,
      stock_shape: document.getElementById("stock-shape").value,
      stock_w: parseFloat(document.getElementById("stock-w").value),
      stock_l: parseFloat(document.getElementById("stock-l").value),
      stock_t: parseFloat(document.getElementById("stock-t").value),
      stock_d: parseFloat(document.getElementById("stock-d") ? document.getElementById("stock-d").value : 90),
      stock_lock_to_model: document.getElementById("stock-lock-to-model").checked,
      margin_xy: parseFloat(document.getElementById("stock-conforming-margin") ? document.getElementById("stock-conforming-margin").value : 5.0),

      stepdown: parseFloat(document.getElementById("cut-stepdown").value),
      stepover: parseFloat(document.getElementById("cut-stepover").value) / 100.0,
      feed_xy: parseFloat(document.getElementById("feed-xy").value),
      feed_z: parseFloat(document.getElementById("feed-z").value),
      tool_d: document.getElementById("tool-select").value === "flat_1_4" ? 6.35 : 3.175,

      // Creality Transforms
      pos_x: parseFloat(document.getElementById("tf-pos-x").value || 0),
      pos_y: parseFloat(document.getElementById("tf-pos-y").value || 0),
      pos_z: parseFloat(document.getElementById("tf-pos-z").value || 0),
      rot_x: parseFloat(document.getElementById("tf-rot-x").value || 0),
      rot_y: parseFloat(document.getElementById("tf-rot-y").value || 0),
      rot_z: parseFloat(document.getElementById("tf-rot-z").value || 0),
      scale_x: parseFloat(document.getElementById("tf-scale-x").value || 100) / 100.0,
      scale_y: parseFloat(document.getElementById("tf-scale-y").value || 100) / 100.0,
      scale_z: parseFloat(document.getElementById("tf-scale-z").value || 100) / 100.0,

      // Infill Pattern & Tabs
      clearing_pattern: document.getElementById("clearing-pattern").value,
      stock_to_leave: parseFloat(document.getElementById("stock-to-leave").value || 0),
      tabs_count: parseInt(document.getElementById("tabs-count").value || 0),
      tab_width: parseFloat(document.getElementById("tab-width").value || 5.0),
      tab_height: parseFloat(document.getElementById("tab-height").value || 2.0)
    };

    const res = await fetch("/api/slice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    currentSlicingData = data;

    // Update stats
    document.getElementById("stat-layers").textContent = data.layer_count;
    document.getElementById("stat-cut-dist").textContent = `${data.cut_distance_mm.toFixed(0)} mm`;
    document.getElementById("stat-rapid-dist").textContent = `${data.rapid_distance_mm.toFixed(0)} mm`;
    document.getElementById("stat-est-time").textContent = `${data.estimated_minutes.toFixed(1)} min`;

    // Configure vertical layer slider
    const slider = document.getElementById("layer-slider");
    slider.min = "0";
    slider.max = String(data.layer_count);
    slider.value = String(data.layer_count);
    document.getElementById("current-layer-label").textContent = `1 - ${data.layer_count}`;

    // Render toolpaths
    renderToolpaths(data.layers);

    document.getElementById("btn-view-gcode").disabled = false;
    document.getElementById("btn-export-gcode").disabled = false;
    document.getElementById("status-text").textContent = `Slicing complete: ${data.layer_count} layers computed. Scrub layer slider or click ▶ to simulate tool.`;
  } catch (err) {
    console.error("Slicing failed:", err);
    document.getElementById("status-text").textContent = "Slicing failed. Check console.";
  } finally {
    sliceBtn.disabled = false;
    sliceBtn.textContent = "🔪 Slice Stock";
  }
}

// Render Toolpaths into 3D Viewport
function renderToolpaths(layers, maxVisibleLayerIndex = 9999) {
  while (toolpathLinesGroup.children.length > 0) {
    toolpathLinesGroup.remove(toolpathLinesGroup.children[0]);
  }

  const cutMat = new THREE.LineBasicMaterial({ color: 0x00ffff, linewidth: 2 });
  const rapidMat = new THREE.LineDashedMaterial({
    color: 0xf87171,
    linewidth: 1,
    dashSize: 3,
    gapSize: 2
  });

  layers.forEach((lyr, lIdx) => {
    if (lIdx > maxVisibleLayerIndex) return;

    lyr.segments.forEach((seg) => {
      const cutPoints = [];
      const rapidPoints = [];

      for (let i = 0; i < seg.points.length - 1; i++) {
        const p1 = seg.points[i];
        const p2 = seg.points[i + 1];

        if (p2.move_type === "cut" || p2.move_type === "plunge") {
          cutPoints.push(new THREE.Vector3(p1.x, p1.y, p1.z));
          cutPoints.push(new THREE.Vector3(p2.x, p2.y, p2.z));
        } else {
          rapidPoints.push(new THREE.Vector3(p1.x, p1.y, p1.z));
          rapidPoints.push(new THREE.Vector3(p2.x, p2.y, p2.z));
        }
      }

      if (cutPoints.length > 0) {
        const geo = new THREE.BufferGeometry().setFromPoints(cutPoints);
        const line = new THREE.LineSegments(geo, cutMat);
        toolpathLinesGroup.add(line);
      }

      if (rapidPoints.length > 0) {
        const geo = new THREE.BufferGeometry().setFromPoints(rapidPoints);
        const line = new THREE.LineSegments(geo, rapidMat);
        line.computeLineDistances();
        toolpathLinesGroup.add(line);
      }
    });
  });
}

// Vertical Layer Slider Scrubbing
function onLayerSliderInput() {
  if (!currentSlicingData) return;
  const val = parseInt(document.getElementById("layer-slider").value);
  const total = currentSlicingData.layer_count;

  if (val === 0 || val === total) {
    document.getElementById("current-layer-label").textContent = `1 - ${total}`;
    renderToolpaths(currentSlicingData.layers, total);
  } else {
    document.getElementById("current-layer-label").textContent = `Pass ${val}`;
    const nominalZ = currentSlicingData.layers[val - 1]?.nominal_z || 0;
    document.getElementById("layer-z-val").textContent = `Z: ${nominalZ.toFixed(2)}mm`;
    renderToolpaths(currentSlicingData.layers, val - 1);
  }
}

// 3D Cutting Simulation Loop
function toggleSimulation() {
  if (!currentSlicingData || currentSlicingData.layers.length === 0) return;

  const simBtn = document.getElementById("btn-play-sim");

  if (isSimulating) {
    clearInterval(simInterval);
    isSimulating = false;
    simBtn.textContent = "▶";
    toolMeshGroup.visible = false;
    document.getElementById("status-text").textContent = "Simulation paused.";
    return;
  }

  const layerVal = parseInt(document.getElementById("layer-slider").value);
  const layersToSim = (layerVal === 0 || layerVal === currentSlicingData.layer_count)
    ? currentSlicingData.layers
    : [currentSlicingData.layers[layerVal - 1]];

  simPoints = [];
  layersToSim.forEach((lyr) => {
    lyr.segments.forEach((seg) => {
      seg.points.forEach((pt) => simPoints.push(pt));
    });
  });

  if (simPoints.length === 0) return;

  isSimulating = true;
  simIndex = 0;
  simBtn.textContent = "⏸";
  toolMeshGroup.visible = true;

  const speedMult = parseFloat(document.getElementById("sim-speed-select").value) || 5;
  const intervalMs = Math.max(10, Math.floor(100 / speedMult));

  simInterval = setInterval(() => {
    if (simIndex >= simPoints.length) {
      clearInterval(simInterval);
      isSimulating = false;
      simBtn.textContent = "▶";
      document.getElementById("status-text").textContent = "Toolpath simulation finished.";
      return;
    }

    const pt = simPoints[simIndex];
    toolMeshGroup.position.set(pt.x, pt.y, pt.z);
    document.getElementById("status-text").textContent = `Simulating: [${pt.move_type.toUpperCase()}] X:${pt.x.toFixed(2)} Y:${pt.y.toFixed(2)} Z:${pt.z.toFixed(2)} mm`;
    document.getElementById("layer-z-val").textContent = `Z: ${pt.z.toFixed(2)}mm`;

    simIndex += Math.ceil(speedMult / 5);
  }, intervalMs);
}

// G-Code View, Machine Verification & Export
async function viewGCode() {
  try {
    const res = await fetch("/api/gcode/text");
    const data = await res.json();
    document.getElementById("gcode-display-text").textContent = data.gcode;
    document.getElementById("gcode-lines-count").textContent = `${data.lines} lines`;

    // Fetch Machine Verification Diagnostics
    try {
      const vRes = await fetch("/api/gcode/verify");
      const vData = await vRes.json();
      if (vData.success && vData.report) {
        const rep = vData.report;
        const badge = document.getElementById("verify-status-badge");
        badge.className = `verify-badge ${rep.status.toLowerCase()}`;
        if (rep.status === "PASS") {
          badge.textContent = `✅ VERIFIED: READY TO RUN (${rep.cutting_blocks} CUT BLOCKS)`;
        } else if (rep.status === "WARNING") {
          badge.textContent = `⚠️ READY WITH ${rep.warnings.length} NOTICE(S)`;
        } else {
          badge.textContent = `❌ ERROR: ${rep.errors[0] || "COLLISION DETECTED"}`;
        }

        const b = rep.bounds;
        document.getElementById("verify-bounds").textContent = `X[${b.x_min}, ${b.x_max}] Y[${b.y_min}, ${b.y_max}] Z[${b.z_min}, ${b.z_max}]`;
        document.getElementById("verify-feed").textContent = `${rep.max_feedrate_xy} mm/min`;
        document.getElementById("verify-spindle").textContent = `${rep.spindle_rpm} RPM`;
      }
    } catch (vErr) {
      console.warn("GCode verification call skipped:", vErr);
    }

    document.getElementById("gcode-modal").style.display = "flex";
  } catch (err) {
    console.error("GCode view error:", err);
  }
}

function copyGCodeToClipboard() {
  const text = document.getElementById("gcode-display-text").textContent;
  navigator.clipboard.writeText(text).then(() => {
    alert("G-Code copied to clipboard!");
  });
}

function closeGCodeModal() {
  document.getElementById("gcode-modal").style.display = "none";
}

function exportGCodeFile() {
  if (!currentSlicingData) return;
  window.location.href = "/api/gcode/download";
}

// Camera Preset Views
function setCameraView(view) {
  const stockW = parseFloat(document.getElementById("stock-w").value);
  const stockL = parseFloat(document.getElementById("stock-l").value);
  const stockT = parseFloat(document.getElementById("stock-t").value);
  let cx = 50 + stockW / 2;
  let cy = 50 + stockL / 2;
  const cz = stockT / 2;

  if (document.getElementById("stock-lock-to-model").checked && currentMesh) {
    cx = currentMesh.position.x;
    cy = currentMesh.position.y;
  }

  controls.target.set(cx, cy, cz);

  if (view === "top") {
    camera.position.set(cx, cy, cz + 250);
  } else if (view === "front") {
    camera.position.set(cx, cy - 250, cz);
  } else if (view === "iso") {
    camera.position.set(cx + 140, cy - 180, cz + 140);
  }
}

// Render loop
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// Bind UI Events
window.addEventListener("DOMContentLoaded", () => {
  initViewport();

  // Model & LiDAR actions
  const fileInput = document.getElementById("stl-file-input");
  document.getElementById("btn-upload-stl").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadStlFile(e.target.files[0]);
    }
  });

  document.getElementById("btn-scan-lidar").addEventListener("click", autoScanBedLidar);
  document.getElementById("btn-load-sample2").addEventListener("click", loadSamplePyramid);
  document.getElementById("btn-slice").addEventListener("click", sliceCurrentModel);

  // G-Code actions
  document.getElementById("btn-view-gcode").addEventListener("click", viewGCode);
  document.getElementById("btn-copy-gcode").addEventListener("click", copyGCodeToClipboard);
  document.getElementById("btn-close-gcode").addEventListener("click", closeGCodeModal);
  document.getElementById("btn-export-gcode").addEventListener("click", exportGCodeFile);

  // Layer scrubber & Simulation
  document.getElementById("layer-slider").addEventListener("input", onLayerSliderInput);
  document.getElementById("btn-play-sim").addEventListener("click", toggleSimulation);

  // Camera presets
  document.getElementById("btn-view-top").addEventListener("click", () => setCameraView("top"));
  document.getElementById("btn-view-front").addEventListener("click", () => setCameraView("front"));
  document.getElementById("btn-view-iso").addEventListener("click", () => setCameraView("iso"));
  document.getElementById("btn-reset-cam").addEventListener("click", () => setCameraView("iso"));
});
