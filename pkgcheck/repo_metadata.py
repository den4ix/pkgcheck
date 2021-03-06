# Copyright: 2006 Brian Harring <ferringb@gmail.com>
# License: BSD/GPL2

from itertools import ifilterfalse, chain, groupby
from operator import attrgetter, itemgetter

from pkgcore.fetch import fetchable
from snakeoil import mappings
from snakeoil.demandload import demandload

from pkgcheck import base, addons

demandload(
    'os',
    'snakeoil.osutils:listdir_dirs,listdir_files,pjoin',
    'snakeoil.sequences:iflatten_instance',
    'pkgcore:fetch',
)


class UnusedGlobalFlags(base.Warning):
    """Unused use.desc flag(s)."""

    __slots__ = ("flags",)

    threshold = base.repository_feed

    def __init__(self, flags):
        super(UnusedGlobalFlags, self).__init__()
        # tricky, but it works; atoms have the same attrs
        self.flags = tuple(sorted(flags))

    @property
    def short_desc(self):
        return "use.desc unused flag%s: %s" % (
            's'[len(self.flags) == 1:], ', '.join(self.flags))


class UnusedGlobalFlagsCheck(base.Template):
    """Check for unused use.desc entries."""

    feed_type = base.versioned_feed
    scope = base.repository_scope
    required_addons = (addons.UseAddon,)
    known_results = (UnusedGlobalFlags,) + addons.UseAddon.known_results

    def __init__(self, options, iuse_handler):
        base.Template.__init__(self, options)
        self.flags = None
        self.iuse_handler = iuse_handler

    def start(self):
        if not self.options.target_repo.masters:
            self.flags = set(self.iuse_handler.global_iuse - self.iuse_handler.unstated_iuse)

    def feed(self, pkg, reporter):
        if self.flags:
            self.flags.difference_update(pkg.iuse_stripped)

    def finish(self, reporter):
        if self.flags:
            reporter.add_report(UnusedGlobalFlags(self.flags))
            self.flags.clear()


class UnusedLicenses(base.Warning):
    """Unused license(s) detected."""

    __slots__ = ("licenses",)

    threshold = base.repository_feed

    def __init__(self, licenses):
        super(UnusedLicenses, self).__init__()
        self.licenses = tuple(sorted(licenses))

    @property
    def short_desc(self):
        return "unused license%s: %s" % (
            's'[len(self.licenses) == 1:], ', '.join(self.licenses))


class UnusedLicensesCheck(base.Template):
    """Check for unused license files."""

    feed_type = base.versioned_feed
    scope = base.repository_scope
    known_results = (UnusedLicenses,)

    def __init__(self, options):
        base.Template.__init__(self, options)
        self.licenses = None

    def start(self):
        master_licenses = set()
        for repo in self.options.target_repo.masters:
            master_licenses.update(repo.licenses)
        self.licenses = set(self.options.target_repo.licenses) - master_licenses

    def feed(self, pkg, reporter):
        self.licenses.difference_update(iflatten_instance(pkg.license))

    def finish(self, reporter):
        if self.licenses:
            reporter.add_report(UnusedLicenses(self.licenses))
        self.licenses = None


class UnusedMirrors(base.Warning):
    """Unused mirrors detected."""

    __slots__ = ("mirrors",)

    threshold = base.repository_feed

    def __init__(self, mirrors):
        super(UnusedMirrors, self).__init__()
        self.mirrors = tuple(sorted(mirrors))

    @property
    def short_desc(self):
        return ', '.join(self.mirrors)


class UnusedMirrorsCheck(base.Template):
    """Check for unused mirrors."""

    required_addons = (addons.UseAddon,)
    feed_type = base.versioned_feed
    scope = base.repository_scope
    known_results = (UnusedMirrors,) + addons.UseAddon.known_results

    def __init__(self, options, iuse_handler):
        base.Template.__init__(self, options)
        self.mirrors = None
        self.iuse_filter = iuse_handler.get_filter('fetchables')

    def start(self):
        repo = self.options.target_repo
        repo_mirrors = set(repo.mirrors.iterkeys())
        master_mirrors = set(x for master in repo.masters for x in master.mirrors.iterkeys())
        self.mirrors = repo_mirrors.difference(master_mirrors)

    def feed(self, pkg, reporter):
        if self.mirrors:
            mirrors = []
            for f in self.iuse_filter((fetchable,), pkg, pkg.fetchables, reporter):
                for m in f.uri.visit_mirrors(treat_default_as_mirror=False):
                    mirrors.append(m[0].mirror_name)
            self.mirrors.difference_update(mirrors)

    def finish(self, reporter):
        if self.mirrors:
            reporter.add_report(UnusedMirrors(self.mirrors))
        self.mirrors = None


class UnknownProfileArches(base.Warning):
    """Unknown arches used in profiles."""

    __slots__ = ("arches",)

    threshold = base.repository_feed

    def __init__(self, arches):
        super(UnknownProfileArches, self).__init__()
        self.arches = arches

    @property
    def short_desc(self):
        return "[ %s ]" % ', '.join(self.arches)


class ArchesWithoutProfiles(base.Warning):
    """Arches without corresponding profile listings."""

    __slots__ = ("arches",)

    threshold = base.repository_feed

    def __init__(self, arches):
        super(ArchesWithoutProfiles, self).__init__()
        self.arches = arches

    @property
    def short_desc(self):
        return "[ %s ]" % ', '.join(self.arches)


class UnknownProfileStatus(base.Warning):
    """Unknown status used for profiles."""

    __slots__ = ("status",)

    threshold = base.repository_feed

    def __init__(self, status):
        super(UnknownProfileStatus, self).__init__()
        self.status = status

    @property
    def short_desc(self):
        return "[ %s ]" % ', '.join(self.status)


class NonexistentProfilePath(base.Warning):
    """Specified profile path doesn't exist."""

    __slots__ = ("path",)

    threshold = base.repository_feed

    def __init__(self, path):
        super(NonexistentProfilePath, self).__init__()
        self.path = path

    @property
    def short_desc(self):
        return self.path


class UnknownCategories(base.Warning):
    """Category directories that aren't listed in a repo's categories.

    Or the categories of the repo's masters as well.
    """

    __slots__ = ("categories",)

    threshold = base.repository_feed

    def __init__(self, categories):
        super(UnknownCategories, self).__init__()
        self.categories = categories

    @property
    def short_desc(self):
        return "[ %s ]" % ', '.join(self.categories)


class RepoProfilesReport(base.Template):
    """Scan repo for various profiles directory issues.

    Including unknown arches in profiles, arches without profiles, and unknown
    categories.
    """

    feed_type = base.repository_feed
    scope = base.repository_scope
    known_results = (
        UnknownProfileArches, ArchesWithoutProfiles,
        NonexistentProfilePath, UnknownProfileStatus, UnknownCategories)

    def __init__(self, options):
        base.Template.__init__(self, options)
        self.arches = options.target_repo.config.known_arches
        self.profiles = options.target_repo.config.profiles.arch_profiles
        self.repo = options.target_repo

    def feed(self, pkg, reporter):
        pass

    def finish(self, reporter):
        category_dirs = set(ifilterfalse(
            self.repo.false_categories.__contains__,
            (x for x in listdir_dirs(self.repo.location) if x[0] != '.')))
        unknown_categories = category_dirs.difference(self.repo.categories)
        if unknown_categories:
            reporter.add_report(UnknownCategories(unknown_categories))

        profile_arches = set(self.profiles.iterkeys())
        unknown_arches = profile_arches.difference(self.arches)
        arches_without_profiles = self.arches.difference(profile_arches)

        if unknown_arches:
            reporter.add_report(UnknownProfileArches(unknown_arches))
        if arches_without_profiles:
            reporter.add_report(ArchesWithoutProfiles(arches_without_profiles))

        profile_status = set()
        for path, status in chain.from_iterable(self.profiles.itervalues()):
            if not os.path.exists(pjoin(self.repo.location, 'profiles', path)):
                reporter.add_report(NonexistentProfilePath(path))
            profile_status.add(status)

        if self.repo.repo_id == 'gentoo':
            accepted_status = ('stable', 'dev', 'exp')
            unknown_status = profile_status.difference(accepted_status)
            if unknown_status:
                reporter.add_report(UnknownProfileStatus(unknown_status))


class UnknownLicenses(base.Warning):
    """License(s) listed in license group(s) that don't exist."""

    __slots__ = ("group", "licenses")

    threshold = base.repository_feed

    def __init__(self, group, licenses):
        super(UnknownLicenses, self).__init__()
        self.group = group
        self.licenses = licenses

    @property
    def short_desc(self):
        return "license group %r has unknown license%s: [ %s ]" % (
                self.group, 's'[len(self.licenses) == 1:], ', '.join(self.licenses))


class LicenseGroupsCheck(base.Template):
    """Scan license groups for unknown licenses."""

    feed_type = base.repository_feed
    scope = base.repository_scope
    known_results = (UnknownLicenses,)

    def __init__(self, options):
        base.Template.__init__(self, options)
        self.repo = options.target_repo

    def feed(self, pkg, reporter):
        pass

    def finish(self, reporter):
        for group, licenses in self.repo.licenses.groups.iteritems():
            unknown_licenses = set(licenses).difference(self.repo.licenses)
            if unknown_licenses:
                reporter.add_report(UnknownLicenses(group, unknown_licenses))


def reformat_chksums(iterable):
    for chf, val1, val2 in iterable:
        if chf == "size":
            yield chf, val1, val2
        else:
            yield chf, "%x" % val1, "%x" % val2


class ConflictingChksums(base.Error):
    """checksum conflict detected between two files"""

    __slots__ = ("category", "package", "version",
                 "filename", "chksums", "others")

    threshold = base.versioned_feed

    _sorter = staticmethod(itemgetter(0))

    def __init__(self, pkg, filename, chksums, others):
        super(ConflictingChksums, self).__init__()
        self._store_cpv(pkg)
        self.filename = filename
        self.chksums = tuple(sorted(reformat_chksums(chksums),
                                    key=self._sorter))
        self.others = tuple(sorted(others))

    @property
    def short_desc(self):
        return "conflicts with (%s) for file %s chksum %s" % (
            ', '.join(self.others), self.filename, self.chksums)


class MissingChksum(base.Warning):
    """a file in the chksum data lacks required checksums"""

    threshold = base.versioned_feed
    __slots__ = ('category', 'package', 'version', 'filename', 'missing',
                 'existing')

    def __init__(self, pkg, filename, missing, existing):
        super(MissingChksum, self).__init__()
        self._store_cpv(pkg)
        self.filename, self.missing = filename, tuple(sorted(missing))
        self.existing = tuple(sorted(existing))

    @property
    def short_desc(self):
        return '"%s" missing required chksums: %s; has chksums: %s' % \
            (self.filename, ', '.join(self.missing), ', '.join(self.existing))


class MissingManifest(base.Error):
    """SRC_URI targets missing from Manifest file"""

    __slots__ = ("category", "package", "version", "files")
    threshold = base.versioned_feed

    def __init__(self, pkg, files):
        super(MissingManifest, self).__init__()
        self._store_cpv(pkg)
        self.files = files

    @property
    def short_desc(self):
        return "distfile%s missing from Manifest: [ %s ]" % (
            's'[len(self.files) == 1:], ', '.join(sorted(self.files)),)


class UnknownManifest(base.Warning):
    """Manifest entries not matching any SRC_URI targets"""

    __slots__ = ("category", "package", "files")
    threshold = base.package_feed

    def __init__(self, pkg, files):
        super(UnknownManifest, self).__init__()
        self._store_cp(pkg)
        self.files = files

    @property
    def short_desc(self):
        return "unknown distfile%s in Manifest: [ %s ]" % (
            's'[len(self.files) == 1:], ', '.join(sorted(self.files)),)


class ManifestReport(base.Template):
    """Manifest related checks.

    Verify that the Manifest file exists, doesn't have missing or
    extraneous entries, and that the required hashes are in use.
    """

    required_addons = (addons.UseAddon,)
    feed_type = base.package_feed
    known_results = (MissingChksum, MissingManifest, UnknownManifest) + \
        addons.UseAddon.known_results

    repo_grabber = attrgetter("repo")

    def __init__(self, options, iuse_handler):
        base.Template.__init__(self, options)
        self.required_checksums = mappings.defaultdictkey(
            lambda repo: frozenset(repo.config.manifests.hashes if hasattr(repo, 'config') else ()))
        self.seen_checksums = {}
        self.iuse_filter = iuse_handler.get_filter('fetchables')

    def feed(self, full_pkgset, reporter):
        # sort it by repo.
        for repo, pkgset in groupby(full_pkgset, self.repo_grabber):
            required_checksums = self.required_checksums[repo]
            pkgset = list(pkgset)
            manifests = set(pkgset[0].manifest.distfiles.iterkeys())
            seen = set()
            for pkg in pkgset:
                pkg.release_cached_data()
                fetchables = set(self.iuse_filter(
                    (fetch.fetchable,), pkg,
                    pkg._get_attr['fetchables'](
                        pkg, allow_missing_checksums=True, ignore_unknown_mirrors=True),
                    reporter))
                pkg.release_cached_data()

                fetchable_files = set(f.filename for f in fetchables)
                missing_manifests = fetchable_files.difference(manifests)
                if missing_manifests:
                    reporter.add_report(MissingManifest(pkg, missing_manifests))

                for f_inst in fetchables:
                    if f_inst.filename in seen:
                        continue
                    missing = required_checksums.difference(f_inst.chksums)
                    if f_inst.filename not in missing_manifests and missing:
                        reporter.add_report(
                            MissingChksum(pkg, f_inst.filename, missing,
                                          f_inst.chksums))
                    seen.add(f_inst.filename)
                    existing = self.seen_checksums.get(f_inst.filename)
                    if existing is None:
                        existing = ([pkg], dict(f_inst.chksums.iteritems()))
                        continue
                    seen_pkgs, seen_chksums = existing
                    for chf_type, value in seen_chksums.iteritems():
                        our_value = f_inst.chksums.get(chf_type)
                        if our_value is not None and our_value != value:
                            reporter.add_result(ConflictingChksums(
                                pkg, f_inst.filename, f_inst.chksums, seen_chksums))
                            break
                    else:
                        seen_chksums.update(f_inst.chksums)
                        seen_pkgs.append(pkg)

            unknown_manifests = manifests.difference(seen)
            if unknown_manifests:
                reporter.add_report(UnknownManifest(pkgset[0], unknown_manifests))
