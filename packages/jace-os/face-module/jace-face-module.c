// Face unlock for the Jace OS lock screen (a gtklock module).
//
// gtklock only checks your password when you press Enter or click Unlock, so Howdy
// (which runs first in /etc/pam.d/gtklock when face unlock is on) never got a chance
// until you did. This module clicks Unlock for you when the screen locks and when it
// wakes up, so it looks for your face right away. If it can't see you, type your password.
#include <gtk/gtk.h>

const gchar module_name[] = "jace-face";
const guint module_major_version = 5;
const guint module_minor_version = 0;

GOptionEntry module_entries[] = {
	{ NULL },
};

static int self_id;
static guint pending;

static GtkWidget *find_named(GtkWidget *w, const char *name) {
	if (g_strcmp0(gtk_widget_get_name(w), name) == 0) return w;
	if (!GTK_IS_CONTAINER(w)) return NULL;
	GList *children = gtk_container_get_children(GTK_CONTAINER(w));
	GtkWidget *found = NULL;
	for (GList *l = children; l && !found; l = l->next) found = find_named(l->data, name);
	g_list_free(children);
	return found;
}

static gboolean face_on(void) {
	gchar *pam = NULL;
	gboolean on = g_file_get_contents("/etc/pam.d/gtklock", &pam, NULL, NULL) &&
		strstr(pam, "\nauth sufficient pam_howdy.so") != NULL;
	g_free(pam);
	return on;
}

static gboolean scan(gpointer data) {
	pending = 0;
	// The focused lock window (one per screen); any of them unlocks them all.
	GList *tops = gtk_window_list_toplevels();
	GtkWidget *target = NULL;
	for (GList *l = tops; l; l = l->next) {
		GtkWidget *entry = find_named(l->data, "input-field");
		GtkWidget *button = find_named(l->data, "unlock-button");
		if (!entry || !button || !gtk_widget_get_mapped(l->data)) continue;
		// Don't start over something you're typing, or a check that's already running.
		if (gtk_entry_get_text_length(GTK_ENTRY(entry)) > 0 || !gtk_widget_get_sensitive(entry)) {
			target = NULL;
			break;
		}
		if (!target || gtk_window_is_active(GTK_WINDOW(l->data))) target = button;
	}
	g_list_free(tops);
	if (target) gtk_button_clicked(GTK_BUTTON(target));
	return G_SOURCE_REMOVE;
}

static void scan_soon(guint ms) {
	if (pending || !face_on()) return;
	pending = g_timeout_add(ms, scan, NULL);
}

void on_activation(void *gtklock, int id) {
	self_id = id;
}

void on_locked(void *gtklock) {
	scan_soon(400);
}

void on_idle_show(void *gtklock) {
	scan_soon(150);
}
