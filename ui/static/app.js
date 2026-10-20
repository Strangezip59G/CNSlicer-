// CNC Milling Slicer — Subtractive Studio Version 1
let scene, camera, renderer, controls;
let bedGrid, stockMesh, partMeshGroup, toolpathLinesGroup, toolMeshGroup;
let currentSlicingData = null;
let currentModelType = "sample2";
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

  // Orbit controls
  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.target.set(60, 40, 10);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;

  // Lighting
  const ambLight = new THREE.AmbientLight(0xffffff, 0.6);
  scene.add(ambLight);

  const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.8);
  dirLight1.position.set(200, 200, 300);
  scene.add(dirLight1);

  const dirLight2 = new THREE.DirectionalLight(0x90caf9, 0.4);
  dirLight2.position.set(-200, -200, 100);
  scene.add(dirLight2);

  // Spoilboard Bed (400 x 400 mm)
  createBedGrid(400, 400);

  // Groups
  partMeshGroup = new THREE.Group();
  toolpathLinesGroup = new THREE.Group();
  scene.add(partMeshGroup);
  scene.add(toolpathLinesGroup);

  // 3D Cutting Tool Mesh
  toolMeshGroup = createToolMesh(3.175);
  scene.add(toolMeshGroup);
  toolMeshGroup.visible = false;

  // Initial default stock block (60x60x30 mm for sample pyramid)
  updateStockMesh(60, 60, 30, 50, 50);

  // Load default sample 3D pyramid
  loadSamplePyramid();

  // Resize handler
  window.addEventListener("resize", onWindowResize);

  // Drag-and-drop STL handler on canvas
  setupDragAndDrop(container);

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

function updateStockMesh(w, l, t, x0 = 50, y0 = 50) {
  if (stockMesh) scene.remove(stockMesh);

  const geo = new THREE.BoxGeometry(w, l, t);
  const mat = new THREE.MeshPhysicalMaterial({
    color: 0x10b981, // Translucent emerald
    transparent: true,
    opacity: 0.28,
    roughness: 0.3,
    transmission: 0.6,
    thickness: 1.0,
    depthWrite: false
  });

  stockMesh = new THREE.Mesh(geo, mat);
  stockMesh.position.set(x0 + w / 2, y0 + l / 2, t / 2);

  const edges = new THREE.EdgesGeometry(geo);
  const lineMat = new THREE.LineBasicMaterial({ color: 0x34d399, linewidth: 1.5 });
  const wire = new THREE.LineSegments(edges, lineMat);
  stockMesh.add(wire);

  scene.add(stockMesh);
  controls.target.set(x0 + w / 2, y0 + l / 2, t / 2);
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

  // ER11 Spindle Collet (black cone)
  const colletGeo = new THREE.CylinderGeometry(10, 5, 20, 16);
  colletGeo.rotateX(Math.PI / 2);
  const colletMat = new THREE.MeshStandardMaterial({ color: 0x1f2937, metalness: 0.8, roughness: 0.5 });
  const collet = new THREE.Mesh(colletGeo, colletMat);
  collet.position.set(0, 0, 35);
  group.add(collet);

  return group;
}

// Load 3D Mesh into Scene
function display3DMesh(vertices, faces, metadata) {
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

  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  const mat = new THREE.MeshStandardMaterial({
    color: 0xf59e0b, // Amber gold part
    roughness: 0.35,
    metalness: 0.65
  });

  const mesh = new THREE.Mesh(geometry, mat);

  // Position model centered on stock
  const stockW = parseFloat(document.getElementById("stock-w").value);
  const stockL = parseFloat(document.getElementById("stock-l").value);
  mesh.position.set(50 + stockW / 2, 50 + stockL / 2, 0);

  partMeshGroup.add(mesh);

  // Update UI metadata panel
  document.getElementById("model-name-badge").textContent = metadata.name || "3D Model";
  document.getElementById("model-dim-x").value = metadata.width.toFixed(1);
  document.getElementById("model-dim-y").value = metadata.length.toFixed(1);
  document.getElementById("model-dim-z").value = metadata.height.toFixed(1);
  document.getElementById("model-poly-count").value = metadata.faces_count || faces.length;

  document.getElementById("status-text").textContent = `Loaded 3D Model: ${metadata.name} (${metadata.width.toFixed(1)} x ${metadata.length.toFixed(1)} x ${metadata.height.toFixed(1)} mm)`;
}

// Load Sample 2 (Stepped Pyramid 3D model)
async function loadSamplePyramid() {
  currentModelType = "sample2";
  try {
    const res = await fetch("/api/model/sample_pyramid");
    const data = await res.json();
    if (data.success) {
      document.getElementById("stock-w").value = 60;
      document.getElementById("stock-l").value = 60;
      document.getElementById("stock-t").value = 30;
      updateStockMesh(60, 60, 30, 50, 50);
      display3DMesh(data.vertices, data.faces, data.metadata);
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

    // Auto-fit stock around model with 10mm XY margin and 2mm Z top allowance
    const autoW = Math.ceil(data.metadata.width + 20);
    const autoL = Math.ceil(data.metadata.length + 20);
    const autoT = Math.ceil(data.metadata.height + 2);

    document.getElementById("stock-w").value = autoW;
    document.getElementById("stock-l").value = autoL;
    document.getElementById("stock-t").value = autoT;

    updateStockMesh(autoW, autoL, autoT, 50, 50);
    display3DMesh(data.vertices, data.faces, data.metadata);

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
  badge.textContent = "Scanning Bed...";
  badge.style.background = "#7e22ce";

  document.getElementById("status-text").textContent = "Toolhead LiDAR sweeping machine bed to locate stock coordinates & surface flatness...";

  try {
    const res = await fetch("/api/lidar/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bed_w: 400, bed_l: 400,
        stock_x: 50, stock_y: 50,
        stock_w: parseFloat(document.getElementById("stock-w").value),
        stock_l: parseFloat(document.getElementById("stock-l").value),
        stock_t: parseFloat(document.getElementById("stock-t").value)
      })
    });
    const data = await res.json();

    if (data.detected) {
      document.getElementById("stock-pos").value = `X: ${data.x_min.toFixed(1)}, Y: ${data.y_min.toFixed(1)}`;
      document.getElementById("surface-warp-val").textContent = `${data.max_deviation.toFixed(2)} mm (Auto-Leveled)`;

      updateStockMesh(data.width, data.length, data.thickness, data.x_min, data.y_min);
      badge.textContent = "Stock Located (Auto)";
      badge.style.background = "#065f46";
      document.getElementById("status-text").textContent = `LiDAR Detection: Located ${data.width.toFixed(1)} x ${data.length.toFixed(1)} x ${data.thickness.toFixed(1)} mm stock. Auto-leveling active.`;
    }
  } catch (err) {
    console.error("Scan error:", err);
  }
}

// Slicing Core
async function sliceCurrentModel() {
  const sliceBtn = document.getElementById("btn-slice");
  sliceBtn.disabled = true;
  sliceBtn.textContent = "Slicing...";
  document.getElementById("status-text").textContent = "Calculating Subtractive Slices: Stock(Z) - Part(Z)...";

  try {
    const payload = {
      model_type: currentModelType,
      stock_w: parseFloat(document.getElementById("stock-w").value),
      stock_l: parseFloat(document.getElementById("stock-l").value),
      stock_t: parseFloat(document.getElementById("stock-t").value),
      stepdown: parseFloat(document.getElementById("cut-stepdown").value),
      stepover: parseFloat(document.getElementById("cut-stepover").value) / 100.0,
      feed_xy: parseFloat(document.getElementById("feed-xy").value),
      feed_z: parseFloat(document.getElementById("feed-z").value),
      tool_d: document.getElementById("tool-select").value === "flat_1_4" ? 6.35 : 3.175
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

    lyr.segments.forEach(seg => {
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
    // Stop simulation
    clearInterval(simInterval);
    isSimulating = false;
    simBtn.textContent = "▶";
    toolMeshGroup.visible = false;
    document.getElementById("status-text").textContent = "Simulation paused.";
    return;
  }

  // Extract all points for currently selected layer (or all layers)
  const layerVal = parseInt(document.getElementById("layer-slider").value);
  const layersToSim = (layerVal === 0 || layerVal === currentSlicingData.layer_count)
    ? currentSlicingData.layers
    : [currentSlicingData.layers[layerVal - 1]];

  simPoints = [];
  layersToSim.forEach(lyr => {
    lyr.segments.forEach(seg => {
      seg.points.forEach(pt => simPoints.push(pt));
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

// G-Code View & Export
async function viewGCode() {
  try {
    const res = await fetch("/api/gcode/text");
    const data = await res.json();
    document.getElementById("gcode-display-text").textContent = data.gcode;
    document.getElementById("gcode-lines-count").textContent = `${data.lines} lines`;
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
  const cx = 50 + stockW / 2;
  const cy = 50 + stockL / 2;
  const cz = stockT / 2;

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
