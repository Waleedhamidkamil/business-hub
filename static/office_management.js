(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const api = () => window.pywebview && window.pywebview.api;
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

    async function load() {
        const status = $("officeStatus");
        try {
            const data = await api().office_management_summary();
            if (!data.ok) throw new Error(data.error || "Unable to load office status.");
            $("officeUsers").innerHTML = `<table><thead><tr><th>User</th><th>Display</th><th>Role</th><th>Status</th><th>Action</th></tr></thead><tbody>` +
                (data.users || []).map((u) => `<tr><td>${esc(u.user)}</td><td>${esc(u.display)}</td><td>${esc(u.role)}</td><td>${u.enabled ? "Enabled" : "Disabled"}</td><td><button class="secondary office-toggle" data-user="${esc(u.user)}" data-enabled="${u.enabled}">${u.enabled ? "Disable" : "Enable"}</button> <button class="secondary office-revoke" data-user="${esc(u.user)}">Revoke sessions</button></td></tr>`).join("") +
                "</tbody></table>";
            $("officeServer").innerHTML = `<p>Disk free: ${Math.round((data.disk.free || 0) / 1073741824)} GB</p>` +
                `<p>Running engines: ${(data.engines || []).filter((e) => e.alive).length}</p>`;
            document.querySelectorAll(".office-toggle").forEach((button) => {
                button.onclick = async () => {
                    const enabled = button.dataset.enabled !== "true";
                    await api().office_set_enabled(button.dataset.user, enabled);
                    await load();
                };
            });
            document.querySelectorAll(".office-revoke").forEach((button) => {
                button.onclick = async () => {
                    await api().office_revoke_session(button.dataset.user);
                    status.textContent = `Sessions revoked for ${button.dataset.user}`;
                };
            });
            status.textContent = "Updated just now";
        } catch (error) {
            status.textContent = error.message || "Unable to load office management.";
        }
    }

    async function showOfficeManagement() {
        if (!api()) return;
        document.querySelectorAll(".view").forEach((view) => { view.hidden = true; });
        $("officeManagementView").hidden = false;
        if (window.setBrand) window.setBrand("Office management");
        await load();
    }

    window.showOfficeManagement = showOfficeManagement;
    window.initOfficeManagement = function () {
        const button = $("dashOfficeBtn");
        if (button) button.onclick = showOfficeManagement;
        if ($("officeBack")) $("officeBack").onclick = () => window.showHub && window.showHub();
        if ($("officeRefresh")) $("officeRefresh").onclick = load;
    };
})();
