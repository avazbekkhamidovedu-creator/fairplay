const fs = require('fs');
const path = require('path');

const tasksDir = path.join(__dirname, 'ai-grading-redesign');
const files = fs.readdirSync(tasksDir).filter(f => f.endsWith('.json'));

let errors = [];

// Check task.json
const task = JSON.parse(fs.readFileSync(path.join(tasksDir, 'task.json'), 'utf-8'));
console.log('=== Task Validation ===');
console.log(`id: ${task.id}`);
console.log(`name: ${task.name}`);
console.log(`status: ${task.status}`);
console.log(`subtask_count: ${task.subtask_count}`);

if (task.id !== 'ai-grading-redesign') errors.push('task id mismatch');
if (!task.exit_criteria || task.exit_criteria.length === 0) errors.push('no exit criteria');
if (!task.objective) errors.push('no objective');
console.log(`exit_criteria: ${task.exit_criteria.length} items`);
console.log(`context_files: ${task.context_files.length} items`);
console.log(`reference_files: ${task.reference_files.length} items`);

// Check subtasks
const subtaskFiles = files.filter(f => f.startsWith('subtask_'));
console.log(`\n=== Subtask Validation (${subtaskFiles.length} files) ===`);

if (subtaskFiles.length !== task.subtask_count) {
  errors.push(`subtask_count (${task.subtask_count}) != actual files (${subtaskFiles.length})`);
}

let allIds = new Set();
let depMap = {};

for (const f of subtaskFiles.sort()) {
  const st = JSON.parse(fs.readFileSync(path.join(tasksDir, f), 'utf-8'));
  console.log(`\n${st.seq}: ${st.title}`);
  console.log(`  status: ${st.status}, depends_on: [${st.depends_on.join(', ')}], parallel: ${st.parallel}`);
  console.log(`  agent: ${st.suggested_agent}`);
  console.log(`  context_files: ${Array.isArray(st.context_files) ? st.context_files.length : 0} items`);
  console.log(`  reference_files: ${Array.isArray(st.reference_files) ? st.reference_files.length : 0} items`);
  console.log(`  acceptance_criteria: ${st.acceptance_criteria.length} items`);
  console.log(`  deliverables: ${st.deliverables.length} items`);
  
  allIds.add(st.seq);
  depMap[st.seq] = st.depends_on;
  
  if (!st.id || !st.id.startsWith('ai-grading-redesign-')) errors.push(`${f}: bad id`);
  if (!st.title) errors.push(`${f}: no title`);
  if (!st.acceptance_criteria || st.acceptance_criteria.length === 0) errors.push(`${f}: no acceptance criteria`);
  if (!st.deliverables || st.deliverables.length === 0) errors.push(`${f}: no deliverables`);
  if (st.status === 'pending' && st.depends_on.length === 0 && st.parallel === false) {
    console.log(`  NOTE: No deps but parallel=false — could be parallel`);
  }

  // Check context_files vs reference_files aren't mixed
  if (Array.isArray(st.context_files)) {
    for (const cf of st.context_files) {
      if (typeof cf === 'string' && (cf.endsWith('.py') || cf.startsWith('judge/') || cf.startsWith('templates/') || cf.startsWith('dmoj/'))) {
        errors.push(`${f}: source file "${cf}" in context_files (should be in reference_files)`);
      }
    }
  }
  if (Array.isArray(st.reference_files)) {
    for (const rf of st.reference_files) {
      if (typeof rf === 'string' && rf.startsWith('.opencode/')) {
        errors.push(`${f}: standards file "${rf}" in reference_files (should be in context_files)`);
      }
    }
  }
}

// Check dependencies
console.log(`\n=== Dependency Validation ===`);
for (const [seq, deps] of Object.entries(depMap)) {
  for (const dep of deps) {
    if (!allIds.has(dep)) {
      errors.push(`subtask_${seq}: depends on ${dep} which doesn't exist`);
    }
  }
}

// Check for circular deps
function hasCycle(seq, visited = new Set()) {
  if (visited.has(seq)) return true;
  visited.add(seq);
  for (const dep of (depMap[seq] || [])) {
    if (hasCycle(dep, new Set(visited))) return true;
  }
  return false;
}
for (const seq of allIds) {
  if (hasCycle(seq)) {
    errors.push(`Circular dependency involving ${seq}`);
  }
}

console.log(`\n=== Summary ===`);
if (errors.length === 0) {
  console.log('ALL CHECKS PASSED');
} else {
  console.log(`ERRORS (${errors.length}):`);
  errors.forEach(e => console.log(`  - ${e}`));
}
