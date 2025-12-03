document.addEventListener("DOMContentLoaded", () => {
  const sourceDbSelect = document.getElementById("sourceDb");
  const targetDbSelect = document.getElementById("targetDb");
  const sourceTableSelect = document.getElementById("sourceTableSelect");
  const targetTableSelect = document.getElementById("targetTableSelect");
  const promptInput = document.getElementById("prompt");
  const submitBtn = document.getElementById("submitBtn");
  const loadingSpinner = document.getElementById("loadingSpinner");
  const submitText = document.getElementById("submitText");
  const resultSection = document.getElementById("resultSection");
  const resultPlaceholder = document.getElementById("resultPlaceholder");
  const resultContent = document.getElementById("resultContent");

  // Utility function for API calls with timeout
  async function fetchWithTimeout(url, options = {}, timeout = 60000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(url, {
        ...options,
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      return response;
    } catch (error) {
      console.log("error at the beginning result:", error);
      clearTimeout(timeoutId);
      if (error.name === "AbortError") {
        throw new Error("Request timed out. Please try again.");
      }
      throw error;
    }
  }

  // Show loading state
  function showLoading(message = "Loading...") {
    submitBtn.disabled = true;
    loadingSpinner.style.display = "inline-block";
    submitText.textContent = message;
  }

  // Hide loading state
  function hideLoading() {
    submitBtn.disabled = false;
    loadingSpinner.style.display = "none";
    submitText.textContent = "🚀 Validate Row Counts";
  }

  // Show error message
  function showError(message) {
    console.error("Error:", message);
    alert(`Error: ${message}`);
  }

  // Load available databases
  async function loadDatabases() {
    try {
      showLoading("Loading databases...");

      const response = await fetchWithTimeout("/api/databases", {}, 30000);

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || `HTTP ${response.status}`);
      }

      const databases = await response.json();

      // Clear existing options
      sourceDbSelect.innerHTML = "<option value=''>Select Source DB</option>";
      targetDbSelect.innerHTML = "<option value=''>Select Target DB</option>";

      // Add database options
      databases.forEach((db) => {
        sourceDbSelect.appendChild(new Option(db, db));
        targetDbSelect.appendChild(new Option(db, db));
      });

      console.log(`Loaded ${databases.length} databases:`, databases);
    } catch (error) {
      showError(`Failed to load databases: ${error.message}`);
    } finally {
      hideLoading();
    }
  }

  // Load tables for a specific database
  async function loadTables(database, targetSelect, placeholderText) {
    if (!database) {
      targetSelect.innerHTML = `<option value=''>${placeholderText}</option>`;
      return;
    }

    try {
      showLoading(`Loading tables for ${database}...`);

      const response = await fetchWithTimeout(
        "/api/tables",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ database: database })
        },
        30000
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || `HTTP ${response.status}`);
      }

      const tables = await response.json();

      // Clear and populate options
      targetSelect.innerHTML = `<option value=''>${placeholderText}</option>`;
      tables.forEach((table) => {
        targetSelect.appendChild(new Option(table, table));
      });

      console.log(`Loaded ${tables.length} tables for ${database}:`, tables);
    } catch (error) {
      showError(`Failed to load tables for ${database}: ${error.message}`);
      targetSelect.innerHTML = `<option value=''>${placeholderText}</option>`;
    } finally {
      hideLoading();
    }
  }

  // Event listeners for database selection
  sourceDbSelect.addEventListener("change", () => {
    const db = sourceDbSelect.value;
    loadTables(db, sourceTableSelect, "Choose source tables");
  });

  targetDbSelect.addEventListener("change", () => {
    const db = targetDbSelect.value;
    loadTables(db, targetTableSelect, "Choose target tables");
  });

  // Validate form inputs (multi-select compatible)
  function validateForm() {
    const sourceDb = sourceDbSelect.value.trim();
    const targetDb = targetDbSelect.value.trim();
    const sourceTables = Array.from(sourceTableSelect.selectedOptions).map((o) => o.value);
    const targetTables = Array.from(targetTableSelect.selectedOptions).map((o) => o.value);
    const prompt = promptInput.value.trim();

    if (!sourceDb) {
      showError("Please select a source database.");
      return false;
    }
    if (!targetDb) {
      showError("Please select a target database.");
      return false;
    }
    if (sourceTables.length === 0) {
      showError("Please select at least one source table.");
      return false;
    }
    if (targetTables.length === 0) {
      showError("Please select at least one target table.");
      return false;
    }
    if (!prompt) {
      showError("Please enter a validation condition.");
      return false;
    }

    return true;
  }

  // Submit validation request (multi-table enabled)
  async function submitValidation() {
    if (!validateForm()) {
      return;
    }

    const formData = {
      source_db: sourceDbSelect.value,
      target_db: targetDbSelect.value,
      source_table: Array.from(sourceTableSelect.selectedOptions).map((o) => o.value),
      target_table: Array.from(targetTableSelect.selectedOptions).map((o) => o.value),
      prompt: promptInput.value
    };

    try {
      showLoading("Analyzing data...");
      resultSection.className = "result-section";
      resultPlaceholder.style.display = "flex";
      resultContent.style.display = "none";

      console.log("Submitting validation request:", formData);

      const response = await fetchWithTimeout(
        "/api/validate",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(formData)
        },
        120000
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(
          errorData.error || `HTTP ${response.status}: ${response.statusText}`
        );
      }

      const result = await response.json();
      console.log("Validation result:", result);

      displayResults(result);
    } catch (error) {
      console.error("Validation error:", error);
      displayError(error.message);
    } finally {
      hideLoading();
    }
  }

  // Display validation results
  function displayResults(result) {
    const resultSection = document.getElementById("resultSection");
    const resultPlaceholder = document.getElementById("resultPlaceholder");
    const resultContent = document.getElementById("resultContent");

    // Clear previous content
    resultContent.innerHTML = "";

    const results = result.results || [];

    if (results.length === 0) {
        resultContent.innerHTML = "<p>No results returned.</p>";
        return;
    }

    resultSection.classList.add("has-results");
    resultPlaceholder.style.display = "none";
    resultContent.style.display = "block";

    results.forEach((r, index) => {
        const card = document.createElement("div");
        card.className = "result-card";
        card.style.margin = "20px 0";
        card.style.padding = "15px";
        card.style.border = "1px solid #ddd";
        card.style.borderRadius = "10px";
        card.style.background = "#fafafa";

        card.innerHTML = `
            <h3>Result #${index + 1}</h3>
            <p><strong>Source Table:</strong> ${r.source_table}</p>
            <p><strong>Target Table:</strong> ${r.target_table}</p>

            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-label">Source Count</div>
                    <div class="metric-value">${r.source_count ?? "N/A"}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Target Count</div>
                    <div class="metric-value">${r.target_count ?? "N/A"}</div>
                </div>
            </div>

            <div class="summary-section">
                <div class="summary-title">📝 AI Analysis</div>
                <div class="summary-text">${r.summary || "No summary available"}</div>
            </div>

            <details style="margin-top: 10px;">
                <summary>Show SQL Queries</summary>
                <pre><strong>Source Query:</strong>\n${r.source_query}</pre>
                <pre><strong>Target Query:</strong>\n${r.target_query}</pre>
            </details>
        `;

        resultContent.appendChild(card);
    });
}

  // Display error message
  function displayError(message) {
    resultSection.className = "result-section has-error";
    resultPlaceholder.style.display = "none";
    resultContent.innerHTML = `
      <div class="error-content">
        <h3>❌ Validation Error</h3>
        <p><strong>Error:</strong> ${message}</p>
        <p><strong>Troubleshooting:</strong></p>
        <ul>
          <li>Check if the selected databases and tables exist</li>
          <li>Verify your database connection settings</li>
          <li>Ensure the validation condition is valid SQL syntax</li>
          <li>Try refreshing the page and selecting options again</li>
        </ul>
      </div>
    `;
    resultContent.style.display = "block";
  }

  submitBtn.addEventListener("click", submitValidation);

  promptInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      submitValidation();
    }
  });

  // Initialize
  console.log("Initializing application...");
  loadDatabases();
});