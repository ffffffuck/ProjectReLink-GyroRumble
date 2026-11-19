using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using static ReLink.Texts;

namespace ReLink;

/// (relink) The side, the same on every page: Home, Options and Mods with their icons and colours, then the state card and the
/// small links. A thing is said in one place: the version and the update in the card (not on Home); what Play will show on
/// Home's cards (not in the side); Play in the sky on Home and in the card on the other pages, never both; the install's
/// percentage on its page, the way from the copy to Play in the card. A line in the card only when there is something to say.
public partial class MainWindow
{
    enum Pill { None, Good, Gold, Red, Grey }

    bool repairable;   // (the problem shown is the tools' own: Repair, in the card)

    /// The card's line shows when it says something (MainNote.Text is set from many places: the line follows it)
    void WatchNote()
    {
        DependencyPropertyDescriptor.FromProperty(TextBlock.TextProperty, typeof(TextBlock))
            .AddValueChanged(MainNote, (_, _) => MainNote.Visibility = string.IsNullOrWhiteSpace(MainNote.Text) ? Visibility.Collapsed : Visibility.Visible);
    }

    void SidebarTexts()
    {
        NavHome.Content = L("Accueil", "Home");
        NavOptions.Content = L("Options", "Options");
        Tip(FolderLink, L("Ouvrir le dossier du jeu", "Open the game folder"));
        Tip(HelpLink, L("Aide et questions fréquentes", "Help and common questions"));
        Tip(DiscordLink, L($"Rejoindre le serveur Discord de {Texts.Name}", $"Join the {Texts.Name} Discord server"));
        Tip(ReportLink, L("Signaler un problème sur GitHub", "Report a problem on GitHub"));
        Tip(ModsBadge, L("Des patchs de code sont à reconstruire ou sans effet : voir Mods.", "Some code patches need a build or have no effect: see Mods."));
        UpdateLink.Content = L("Mettre à jour", "Update");
    }

    static void Tip(FrameworkElement e, string text) => e.ToolTip = new ToolTip { Style = St("Tip"), Content = text };

    void OpenReport(object sender, RoutedEventArgs e) => Open(RepoPage + "/issues/new/choose");

    /// The card's head: what it is about, and its state in a coloured pill (none: no pill)
    void Card(string title, string pill = "", Pill kind = Pill.None)
    {
        CardTitle.Text = title;
        StatePill.Visibility = kind == Pill.None || pill == "" ? Visibility.Collapsed : Visibility.Visible;
        StatePillText.Text = pill;
        (StatePill.Background, StatePillText.Foreground) = kind switch
        {
            Pill.Good => (Res("Green"), (Brush)Brushes.White),
            Pill.Gold => (Res("Yellow"), new SolidColorBrush(Color.FromRgb(0x41, 0x24, 0x02))),
            Pill.Red => (Res("Red"), Brushes.White),
            _ => (new SolidColorBrush(Color.FromRgb(0x4A, 0x45, 0x55)), Res("SideText")),
        };
    }

    /// The way from the player's copy to Play, in the card while the game is installed or built: done, here (or stopped), to come.
    /// (the shaders are the game's own first start, its loading screen: said here so the wait there is not a surprise)
    void Steps(int at, bool stopped = false)
    {
        StepList.Children.Clear();
        var names = new[]
        {
            updating ? L("La mise à jour", "The update") : L("Votre copie", "Your copy"),
            L("Construction", "Build"),
            L("Shaders (1er lancement)", "Shaders (1st start)"),
            L("Jouer", "Play"),
        };
        for (var i = 0; i < names.Length; i++)
        {
            var done = i < at;
            var here = i == at;
            var mark = done ? "" : here && stopped ? "" : here ? "" : "";
            var colour = done ? Res("Green") : here && stopped ? Res("Red") : here ? Res("SideText") : Res("SideSoft");
            var row = new DockPanel { Margin = new Thickness(0, 2, 0, 2) };   // (a long name wraps under itself, not cut)
            var icon = new TextBlock { Text = mark, FontFamily = new FontFamily("Segoe Fluent Icons, Segoe MDL2 Assets"), FontSize = 12, Width = 20,
                                       Foreground = colour, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 3, 0, 0) };
            DockPanel.SetDock(icon, Dock.Left);
            row.Children.Add(icon);
            row.Children.Add(new TextBlock { Text = names[i], FontSize = 13, TextWrapping = TextWrapping.Wrap, Foreground = here ? Res("SideText") : Res("SideSoft"),
                                             FontWeight = here ? FontWeights.Bold : FontWeights.Normal });
            StepList.Children.Add(row);
        }
        StepList.Visibility = Visibility.Visible;
    }

    /// While an update or a build again runs, Home, Options and Mods can be looked at: the card then leads back to the progress
    bool Browsing => view == View.Home && (runner != null || previewBuilding);
    bool CanBrowse => (runner != null || previewBuilding) && updating && Game.Installed(outPath);
    bool previewBuilding;   // (the snapshot's "home-building": Home looked at during an update)

    /// "Install again" left the installed game: Home, Options and Mods lead back to it
    bool BackToHome => view == View.Install && runner == null && s.Folder != "" && Game.Installed(s.Folder);

    /// The side for the page shown: called by Show and by Home's own pages
    void UpdateSide()
    {
        var home = view == View.Home;
        var hero = home && NavHome.IsChecked == true;
        NavPanel.IsEnabled = home || (view == View.Build && CanBrowse) || BackToHome;
        if (view != View.Home) foreach (var n in new[] { NavHome, NavOptions, NavMods }) n.IsChecked = false;   // (none lit: no Home page shows)
        FolderLink.Visibility = s.Folder != "" && Game.Installed(s.Folder) ? Visibility.Visible : Visibility.Collapsed;
        StepList.Visibility = view is View.Install or View.Build or View.Problem || Browsing ? Visibility.Visible : Visibility.Collapsed;
        HeroPlay.IsEnabled = runner == null && game == null && Game.Installed(s.Folder);
        switch (view)
        {
            case View.Install:
                Card(L("Installation", "Install"));
                Steps(0);
                UpdateLink.Visibility = Visibility.Collapsed;
                MainButton.Visibility = Visibility.Visible;   // (Install, or Update ReLink: Install.cs RefreshMode)
                break;
            case View.Build:
                Card(updating ? L("Mise à jour", "Update") : L("Installation", "Install"), L("En cours", "Running"), Pill.Grey);
                Steps(1);
                MainNote.Text = CanBrowse ? L("Vous pouvez regarder les autres pages en attendant.", "You can look at the other pages meanwhile.") : "";
                UpdateLink.Visibility = Visibility.Collapsed;
                MainButton.Visibility = Visibility.Collapsed;   // (the percentage and Cancel are the page's)
                break;
            case View.Problem:
                Card(updating ? L("Mise à jour", "Update") : L("Installation", "Install"), L("Arrêtée", "Stopped"), Pill.Red);
                Steps(1, stopped: true);
                MainNote.Text = "";
                UpdateLink.Visibility = Visibility.Collapsed;
                // (Repair is the card's when the tools are to repair; otherwise the page's Back and report are the way on)
                MainButton.Visibility = repairable ? Visibility.Visible : Visibility.Collapsed;
                MainButtonText.Text = L("Réparer", "Repair");
                MainButton.IsEnabled = true;
                break;
            case View.Home when Browsing:
                Card(updating ? L("Mise à jour", "Update") : L("Installation", "Install"), L("En cours", "Running"), Pill.Grey);
                Steps(1);
                UpdateLink.Visibility = Visibility.Collapsed;
                MainButton.Visibility = Visibility.Visible;
                MainButtonText.Text = L("Progression", "Progress");
                MainButton.IsEnabled = true;
                break;
            case View.Home:
                MainButton.Visibility = hero ? Visibility.Collapsed : Visibility.Visible;
                MainButtonText.Text = L("Jouer", "Play");
                MainButton.IsEnabled = Game.Installed(s.Folder) && game == null;
                break;
        }
    }

    /// The version and the update, in the card on Home: the version as its head; up to date, out (with Update), or unknown
    void ShowVersion(string? version, string? next, bool canUpdate, bool @checked)
    {
        if (version == null)
        {
            Card(L("Version de dev", "Dev build"));   // (built from the sources: no release number)
            UpdateLink.Visibility = Visibility.Collapsed;
            MainNote.Text = next != null ? L($"Dernière version publiée : {next}.", $"Latest release: {next}.") : "";
            return;
        }
        UpdateLink.Visibility = Visibility.Collapsed;
        if (!@checked) { Card(version, L("Non vérifié", "Not checked"), Pill.Grey); return; }
        if (next == null || next == version) { Card(version, L("À jour", "Up to date"), Pill.Good); return; }
        Card(version, L($"{next} dispo", $"{next} out"), Pill.Gold);
        if (canUpdate)
        {
            UpdateLink.Visibility = Visibility.Visible;
            Tip(UpdateLink, L("La mise à jour garde vos sauvegardes, vos réglages et vos mods. Le jeu est reconstruit ; le prochain lancement peut préparer les shaders à nouveau.",
                              "The update keeps your saves, settings and mods. The game is built again; the next start may prepare the shaders again."));
            MainNote.Text = L("Garde vos sauvegardes, réglages et mods.", "Keeps your saves, settings and mods.");
        }
        else MainNote.Text = L($"Installez de nouveau le jeu pour avoir la {next}.", $"Install the game again to get {next}.");
    }
}
